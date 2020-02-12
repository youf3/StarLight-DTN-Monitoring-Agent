#!/bin/bash

#set -x

systemd_loc=/etc/systemd/system/
speed=100000

get_arch () {
    architecture=""
    case $(uname -m) in
	i386)   architecture="386" ;;
	i686)   architecture="386" ;;
	x86_64) architecture="amd64" ;;
	arm)    dpkg --print-architecture | grep -q "arm64" && architecture="arm64" || architecture="arm" ;;
	ppc64le) architecture="ppc64le" ;;
    esac
    #echo $architecture
}

get_linux_dist() {

    if [ -f /etc/os-release ]; then
	# freedesktop.org and systemd
	. /etc/os-release
	OS=$NAME
    else
	# Fall back to uname, e.g. "Linux <version>", also works for BSD, etc.
	OS=$(uname -s)
    fi
}

test_cmd () {

    name=$1
    cmd=$2
    #echo $cmd

    eval $cmd
    RETVAL=$?
    if [ $RETVAL -ne 0 ]
    then
	echo "$name not found"
	exit -1
    fi
    return 0
}

check_requirements () {

    if [ ! -d "node-exporter-textfile-collector-scripts" ]
    then
	echo "submodule is missing"
	exit -1
    fi

    if [ ! -d "prometheus-ethtool-exporter" ]
    then
	echo "submodule is missing"
	exit -1
    fi

    get_linux_dist

    echo "$OS"

    if [ "$OS" == "Ubuntu" ]
    then
	sudo apt install -y python3-pip jq pkg-config libnuma-dev libnl-3-dev moreutils libnl-route-3-dev ethtool # lldpd
    elif [ "$OS" == "CentOS Linux" ]
    then
	sudo yum install -y epel-release
	sudo yum install -y python36-pip jq pkgconfig numactl-libs libnl3-devel ethtool moreutils moreutils python3-devel numactl-devel # lldpd
    else
	echo "Unsupported distribution"
	exit -1
    fi
    
    test_cmd "pip" "sudo pip3 -V > /dev/null"
    sudo -H pip3 install Cython babel
    sudo -H pip3 install -r requirements.txt
    cmd="echo test|sponge > /dev/null"
    test_cmd "sponge" "$cmd"

    test_cmd "jq" "jq -V"

    if [ "$architecture" == ""   ]
    then
	echo "Architucture not known"
	exit -1
    fi

}

install_node_exp () {
    sudo systemctl stop node_exporter.service

    sudo useradd --no-create-home --shell /bin/false node_exporter
    sudo mkdir /etc/node_exporter > /dev/null 2>&1
    
    wget -O build/node_exporter.tar.gz https://github.com/prometheus/node_exporter/releases/download/v0.18.1/node_exporter-0.18.1.linux-${architecture}.tar.gz 

    cd build
    tar -xf node_exporter.tar.gz
    sudo cp node_exporter-0.18.1.linux-amd64/node_exporter /usr/local/bin
    cd ..
    sudo cp systemd/node_exporter.service $systemd_loc
    sudo systemctl daemon-reload
    sudo systemctl start node_exporter.service
    sudo systemctl enable node_exporter.service

    if [ "$OS" == "CentOS Linux" ]
    then
	echo "Adding 9100/tcp to firewall"
	sudo cp node_exporter.xml /usr/lib/firewalld/services/
	sudo firewall-cmd --permanent --zone public --add-service=node_exporter
	sudo firewall-cmd --reload
    fi

}

list_interface () {
    interfaces=()
    phy_interface="phy_int=["
    ind=0
    for interface in /sys/class/net/*
    do
	if [ "$speed" == "$(cat $interface/speed 2> /dev/null )" ]
	then 
	    #echo "${interface##*/}"	    
	    interfaces[$ind]=${interface##*/}
	    ind=$(( ind + 1 ))

	    subvlans="$(ls -d ${interface}/upper_* 2>/dev/null)"
	    if [ ${#subvlans} -gt 0 ]
	    then
		#echo "this is physical device"
		phy_interface="${phy_interface}${interface##*/}|"
	    fi

	fi
    done

    if [ ${#interfaces[@]} -eq 0 ]
    then
	echo "Cannot find interface with speed $speed"
	exit -1
    fi

    phy_interface="${phy_interface%?}]"

}

install_tunedtn () {
    sudo cp TuneDTN.py /usr/local/bin/
    sudo cp set_irq_affinity.sh set_irq_affinity_bynode.sh common_irq_affinity.sh /usr/sbin/
    list_interface
    echo INTERFACES=${interfaces[@]}|sudo tee /etc/node_exporter/env
    sudo cp systemd/tune.service $systemd_loc
    sudo systemctl daemon-reload
    sudo systemctl start tune.service
    sudo systemctl enable tune.service
}

install_nvme_exp () {
    sudo cp node-exporter-textfile-collector-scripts/nvme_metrics.sh /usr/local/bin
    sudo cp systemd/nvme_exporter.* $systemd_loc
    sudo systemctl daemon-reload
    for file in nvme_exporter.service nvme_exporter.timer
    do
	sudo systemctl start $file 
	sudo systemctl enable $file
    done
}

install_ethtool_exp() {
    sudo cp prometheus-ethtool-exporter/ethtool-exporter.py /usr/local/bin
    sudo cp systemd/ethtool_exporter.service $systemd_loc
    sudo systemctl daemon-reload
    sudo systemctl start ethtool_exporter.service
    sudo systemctl enable ethtool_exporter.service
}

configure_lldpd() {
    echo "configure lldp portidsubtype ifname" |sudo tee /etc/lldpd.d/set_port_id.conf
    sudo systemctl restart lldpd
    sudo systemctl enable lldpd
}

## lldp is great, but we lose some packet when lldpd is running.
disable_lldp() {
    sudo systemctl stop lldpd
    sudo systemctl disable lldpd
}

get_arch
check_requirements
git submodule update --init --recursive

mkdir build > /dev/null 2>&1

install_node_exp
install_nvme_exp
install_ethtool_exp
install_tunedtn
# configure_lldpd
# disable_lldp
