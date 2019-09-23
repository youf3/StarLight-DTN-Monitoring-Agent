#!/bin/bash

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

test_cmd () {

    name=$1
    cmd=$2
    #echo $cmd

    eval $cmd
    RETVAL=$?
    if [ $RETVAL -ne 0 ]
    then
	# echo "$name not found"
	exit -1
    fi
}

check_requirements () {
    test_cmd "pip" "sudo pip3 -V > /dev/null"
    
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
}

list_interface () {
    interfaces=()
    ind=0
    for interface in /sys/class/net/*
    do
	if [ "$speed" == "$(cat $interface/speed 2> /dev/null )" ]
	then 
	    #echo "${interface##*/}"	    
	    interfaces[$ind]=${interface##*/}
	    ind=$(( ind + 1 ))
	fi
    done

    if [ ${#interfaces[@]} -eq 0 ]
    then
	echo "Cannot find interface with speed $speed"
	exit -1
    fi
}

install_tunedtn () {
    sudo cp TuneDTN.py /usr/local/bin/
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
    sudo cp set_irq_affinity.sh set_irq_affinity_bynode.sh common_irq_affinity.sh /usr/sbin/
    for file in nvme_exporter.service nvme_exporter.timer
    do
	sudo systemctl start $file 
	sudo systemctl enable $file
    done
}


get_arch
check_requirements
git submodule update --init --recursive

mkdir build > /dev/null 2>&1

install_node_exp
install_nvme_exp
install_tunedtn
