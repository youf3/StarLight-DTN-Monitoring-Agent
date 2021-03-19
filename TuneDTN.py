import time
import getpass
import subprocess
import platform
import re
import pyroute2, ethtool
import argparse

tcp_params = {
        'net.core.rmem_max' : 2147483647,
        'net.core.wmem_max' : 2147483647,
        'net.ipv4.tcp_rmem' : [4096, 87380, 2147483647],
        'net.ipv4.tcp_wmem' : [4096, 87380, 2147483647],
        'net.core.netdev_max_backlog' : 250000,
        'net.ipv4.tcp_no_metrics_save' : 1,
        'net.ipv4.tcp_mtu_probing' : 1,
        'net.core.default_qdisc' : 'fq'
        }

def run_command(cmd, ignore_stderr = False):
    proc = subprocess.Popen(cmd, shell=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, 
                                stderr=subprocess.PIPE)
    try:
        outs, errs = proc.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        if 'sudo: no tty present and no askpass program specified' in str(proc.stderr.readline(),'UTF-8'):
            print('To change system parameters, please input sudo password')
            password = getpass.getpass()    
            cmd = cmd.replace('sudo', 'sudo -S')
            proc = subprocess.Popen(cmd, shell=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, 
                                    stderr=subprocess.PIPE)
            outs, errs = proc.communicate(bytes(password + '\n', 'UTF-8'))
            errs = str(errs,'UTF-8').split('\n')
            for line in errs:
                if '[sudo] password for' in line:
                    errs.remove(line)
            errs = bytes(''.join(errs), 'UTF-8')
            
        elif proc.stderr.readline() == b'':
            outs, errs = proc.communicate()
        else:
            outs, errs = proc.communicate()
        
    return [str(outs,'UTF-8'), str(errs,'UTF-8')]

def test_password():
    command = 'sudo su'
    out, err = run_command(command)
    print(out, err)
    if 'incorrect password attempt' in err:
        return False
    return True
    
def get_phy_int(interface):
    ip = pyroute2.IPRoute()
    if len(ip.link_lookup(ifname=interface)) == 0 :
        return None
    link = ip.link("get", index=ip.link_lookup(ifname=interface)[0])[0]
    raw_link_id = list(filter(lambda x:x[0]=='IFLA_LINK', link['attrs']))
    if len(raw_link_id) == 1:
        #print('This is vlan, Checking raw interface..')
        raw_index = raw_link_id[0][1]
        raw_link = ip.link("get", index=raw_index)[0]
        phy_int=list(filter(lambda x:x[0]=='IFLA_IFNAME', raw_link['attrs']))[0][1]
        return phy_int
    else:
        return interface

def get_link_cap(pci_info):
    line = list(filter(lambda x:'LnkSta:' in x ,pci_info))[0]
    caps = list(filter(None, re.split('[:,\t]',line)))
    
    return caps[1], caps[2]
    
def get_mtu(interface):
    ip = pyroute2.IPRoute()
    if len(ip.link_lookup(ifname=interface)) == 0 :
        return None
    link = ip.link("get", index=ip.link_lookup(ifname=interface)[0])[0]
    MTU = int((list(filter(lambda x:x[0]=='IFLA_MTU', link['attrs'])))[0][1])
    return MTU

def get_numa(phy_int):
    command = 'cat /sys/class/net/{0}/device/numa_node'.format(phy_int)
    output, error = run_command(command)
    if error != '':
        print("Cannot find NUMA Node for {0}".format(phy_int))
        return None
    numa = int(str(output).strip())
    if numa == -1: numa = 0
    return numa    
    
def tune_sysctl():
    print('Changing TCP buffer to 2GB')
    command = ''
    for param in tcp_params:
        if isinstance(tcp_params[param], list):
            value = ' '.join(str(v) for v in tcp_params[param])
        else:
            value = tcp_params[param]
        command += 'sudo sysctl {0}=\'{1}\';'.format(param, value)
    outs, errs = run_command(command)
    if errs != '':
        print(errs)

def tune_fq(phy_int):
    print('Setting fq')
    command = 'tc qdisc del dev {0} root fq; sudo tc qdisc add dev {0} root fq'.format(phy_int)
    outs, errs = run_command(command)
    if errs != '':
        print(errs)
        
def tune_mtu(interface):
    print('Changing MTU to 9k')
    phy_int = get_phy_int(interface)
    command = 'ip link set dev {0} mtu 9000'.format(phy_int)
    if phy_int != interface:        
        command += '; sudo ip link set dev {0} mtu 9000'.format(interface)
    run_command(command)

def tune_cpu_governer():
    print('Setting CPU Governor to Performance')
    command = 'sh -c \'echo performance | tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor\''
    outs, errs = run_command(command)
    if errs != '':
        print(errs)

def tune_mellanox(phy_int):    
    print('Tunning Mellanox card')
    bus = ethtool.get_businfo(phy_int)
    numa = get_numa(phy_int)
    # print(numa)
    command = 'setpci -s {0} 68.w'.format(bus)
    output, error = run_command(command)
    
    tempstr = list(output)
    tempstr[0] = '5'
    maxredreq = ''.join(tempstr)    
        
    command = 'setpci -s {0} 68.w={1}'.format(bus, maxredreq)
    output, error = run_command(command)
    
    if error != '':
        print(error)
        
    # command = 'sudo mlnx_tune -p HIGH_THROUGHPUT'
    # output, error = run_command(command, ignore_stderr= True)
    
    # if error != '':
    #     print(error)
    #print(output)
    
def tune_ring_size(phy_int):
    print('Tunning ring param for ConnectX-4 and 5')
    ring = ethtool.get_ringparam(phy_int)
    ring['rx_pending'] = 8192
    ring['tx_pending'] = 8192
    ethtool.set_ringparam(phy_int, ring)

def tune_flow_control(phy_int):
    print('Turning flow_control on')
    
    command = 'sudo ethtool -A {0} tx on rx on'.format(phy_int)
    output, error = run_command(command)

    if error != '':
        print(error)
    print(output)

def tune_irqbalance():
    print('Turning irqbalance off')
    
    command = 'sudo systemctl stop irqbalance'
    output, error = run_command(command)

    if error != '':
        print(error)
    #print(output)
    
def tune_irq_affinity(interface):
    print('Tuning irq affinity')
    phy_int = get_phy_int(interface)
    numa = get_numa(phy_int)
    command = 'set_irq_affinity.sh {0}'.format(phy_int)
    #print(command)
    output, error = run_command(command) 
    if error != '':
        print(error)
    #print(output)
   
def tune_irq_size(interface, local_cores):
    phy_int = get_phy_int(interface)
    numa = get_numa(phy_int)
    print('Limiting IRQ size to {}'.format(len(local_cores)))
    command = 'sudo ethtool -L {} combined {}'.format(interface, len(local_cores))
    #print(command)
    _, error = run_command(command)

    if error != '' and 'combined unmodified, ignoring' not in error:
        print(error)

    command = 'set_irq_affinity_bynode.sh {} {}'.format(numa, interface)
    #print(command)
    output, error = run_command(command)

    if error != '':
        print(error)
    #print(output)

def tune_dropless_rq(interface):
    phy_int = get_phy_int(interface)
    print('Turning dropless_rq on')

    command = 'ethtool --set-priv-flags {} dropless_rq on'.format(phy_int)
    output, error = run_command(command)

    if error != '':
        print(error)

def get_local_cores(numa):
    import libnuma
    local_cores = []
    cpumask = libnuma.NodeToCpus(numa)
    for i in range(0, libnuma.NumConfiguredCpus()):
        if cpumask.isbitset(i):
            local_cores.append(i)
    return local_cores

def get_cpu_name():
    from cpuinfo import get_cpu_info
    return get_cpu_info()['brand_raw']    

import unittest

class TuningTest(unittest.TestCase):
    
    def __init__(self, testname, interface):
        super(TuningTest, self).__init__(testname)
        self.interface = interface
        self.phy_int = get_phy_int(self.interface) 
        if self.phy_int == None: raise Exception("There is no interface {0}".format(interface))
        #self.driver = ethtool.get_module(self.phy_int)
        self.bus = ethtool.get_businfo(self.phy_int)
    
    def test_sysctl_value(self):
        for command in tcp_params:
            output, error = run_command('sysctl {0}'.format(command))
            if error == '':
                current_val = output.split()[-1]
                tune_param = tcp_params[command]
                #print(tune_param, type(tune_param))
                if isinstance(tune_param, int):
                    self.assertGreaterEqual(int(current_val), tune_param)
                elif isinstance(tune_param, list):
                    self.assertGreaterEqual(int(current_val), int(tune_param[-1]))
                elif isinstance(tune_param, str):
                    self.assertEqual(current_val, tune_param)
            else:
                print(error)
                self.fail(error)
                
    def test_fq(self):        
        command = 'tc qdisc show dev {}'.format(self.phy_int)
        output, error = run_command(command)
        if error != '':
            print(error)
        qm=output.split(' ')[1]
        self.assertEqual('fq', qm)    
    
    def test_mtu(self):        
        if self.phy_int != self.interface:
            self.assertGreaterEqual(get_mtu(self.interface), 9000)  
        self.assertGreaterEqual(get_mtu(self.phy_int), 9000)
        
    def test_cpu_governor(self):
        command = 'cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor'
        output, error = run_command(command)
        
        if error == '':
            governors = output.split('\n')[:-1]
            for govenor in governors:
                self.assertEqual(govenor,'performance')
        
        elif 'No such file or directory' in error : 
            self.skipTest('No CPU scaling governer found.')
        else: self.fail(error)
            
    def test_pci_speed(self):
        command = 'sudo lspci -vvv -s {}'.format(self.bus)
        output, error = run_command(command)
        
        if error == '':
            status = output.split('\n')
            #print(status)
            speed, width = get_link_cap(status)
            self.assertEqual(speed, 'Speed 8GT/s')
            self.assertEqual(width, ' Width x16')
        else:
            self.fail(error)
    
    def test_flow_control(self):
        if self.phy_int == None : self.fail('No interface {}'.format(self.interface))
        
        command = 'ethtool -a {0}'.format(self.phy_int)
        output,error = run_command(command)
        for line in output.split('\n'):
            if 'RX:' in line:
                rx = line.split('\t')[-1]
                self.assertEqual(rx,'on')
            elif 'TX:' in line:
                tx = line.split('\t')[-1]
                self.assertEqual(tx,'on')
        
    def test_irqbalance(self):
        command = 'systemctl'
        output,error = run_command(command)
        if 'System has not been booted with systemd as init system' in error:
            self.skipTest("We are inside container, cannot run systemd")
        command = 'systemctl status irqbalance'
        output,error = run_command(command)
        for line in output.split('\n'):
            if 'Active:' in line:
                rx = line.split(' ')[4]
                self.assertEqual(rx,'inactive')

class CxTest(unittest.TestCase):
    def __init__(self, testname, interface):
        super(CxTest, self).__init__(testname)
        self.interface = interface
        self.phy_int = get_phy_int(self.interface) 
        self.driver = ethtool.get_module(self.phy_int)
        self.bus = ethtool.get_businfo(self.phy_int)

    def test_maxreadreq(self):
        command = 'sudo setpci -s {0} 68.w'.format(self.bus)
        output, error = run_command(command)
        #print(output)
        self.assertEqual(output[0], '5')
            
    def test_ring_size(self):
        command = 'lspci -s {0}'.format(self.bus)
        output,error = run_command(command)
        if '[ConnectX-5' not in output and '[ConnectX-4' not in output : self.skipTest('This is not ConnectX-5')
            
        ring_param = ethtool.get_ringparam(self.phy_int)
        self.assertEqual(ring_param['rx_pending'], 8192)
        self.assertEqual(ring_param['tx_pending'], 8192)

    def test_dropless_rq(self):
        command = 'ethtool --show-priv-flags {}'.format(self.phy_int)
        output,error = run_command(command)

        dropless_rq = None

        for line in output.splitlines():
            if 'dropless_rq' in line:
                dropless_rq = line.split(':')[1].strip()

        if dropless_rq == None : self.skipTest("dropless_rq is not supported. Please install recent Mellanox OFED driver.")
        self.assertEqual(dropless_rq, 'on')

class AMDTest(unittest.TestCase):
    def __init__(self, testname, interface):
        super(AMDTest, self).__init__(testname)
        self.interface = interface
        self.phy_int = get_phy_int(self.interface) 
        self.numa = get_numa(self.phy_int)
        self.local_cores = get_local_cores(self.numa)

    def test_irq_size(self):
        command = 'ethtool -l {}'.format(self.phy_int)
        output,error = run_command(command)

        cur_queue = output.strip().split('\n')[-1].split(':')[1].strip()
        self.assertEqual(int(cur_queue), len(self.local_cores))

    def test_iommu(self):
        with open('/proc/cmdline') as f:
            kernel_cmdline = f.readline()

        self.assertIn('iommu=pt', kernel_cmdline)        

def main(interfaces):    
    tuned_int = []
    cpu = get_cpu_name()

    tune_irqbalance()

    for interface in interfaces:
        phy_int = get_phy_int(interface)
        numa = get_numa(phy_int)
        local_cores = get_local_cores(numa)

        if phy_int is None:
            print("Cannot find interface {0}. Ignoring {0}..".format(interface))
            continue
        else:
            print('Starting Test for {}'.format(interface))
        if phy_int not in tuned_int:
            tune_irq_affinity(phy_int)
            tuned_int.append(phy_int)

        #load generic tests
        test_loader = unittest.TestLoader()
        generic_test_names = test_loader.getTestCaseNames(TuningTest)
        generic_suite = unittest.TestSuite()
        for test_name in generic_test_names:
            generic_suite.addTest(TuningTest(test_name, interface))
        
        print('\n----------------------Starting Generic test---------------------------')
        #generic test
        test_result = unittest.TextTestRunner(verbosity=2).run(generic_suite)
        for failure in test_result.failures:
            testname = failure[0].id().split(".")[-1]
            if testname == 'test_sysctl_value':
                tune_sysctl()
            elif testname == 'test_fq':
                tune_fq(phy_int)
            elif testname == 'test_mtu':
                tune_mtu(interface)
            elif testname == 'test_cpu_governor':
                tune_cpu_governer()
            elif testname == 'test_pci_speed':
                print('Please check the PCI slot for {}'.format(interface))
            elif testname == 'test_flow_control':
                tune_flow_control(phy_int)
            elif testname == 'test_iommu':
                print('Please add iommu=pt to the kernel parameter')     

        print('\n---------------Starting Mellanox specific test------------------------')
        #load mellanox connectx-4 and 5 specific test
        if ethtool.get_module(phy_int) == 'mlx5_core':
            test_names = test_loader.getTestCaseNames(CxTest)
            testsuite = unittest.TestSuite()
            for test_name in test_names:
                testsuite.addTest(CxTest(test_name, interface))
        
            test_result = unittest.TextTestRunner(verbosity=2).run(testsuite)

            for failure in test_result.failures:
                testname = failure[0].id().split(".")[-1]
                if testname == 'test_maxreadreq':
                    tune_mellanox(phy_int)
                elif testname == 'test_ring_size':
                    tune_ring_size(phy_int)
                elif testname == 'test_dropless_rq':
                    tune_dropless_rq(interface)
        
        print('\n------------------Starting AMD specific test--------------------------')        
        if "AMD EPYC 7" in cpu:
            test_names = test_loader.getTestCaseNames(AMDTest)
            testsuite = unittest.TestSuite()
            for test_name in test_names:
                testsuite.addTest(AMDTest(test_name, interface))
        
            test_result = unittest.TextTestRunner(verbosity=2).run(testsuite)

            for failure in test_result.failures:
                testname = failure[0].id().split(".")[-1]
                if testname == 'test_irq_size':
                    tune_irq_size(phy_int,local_cores)
                elif testname == 'test_iommu':
                    print('Please add iommu=pt to the kernel parameter')

        print('Done')

if __name__ == '__main__':
    interfaces = []
    ded_ifs = ['lo', 'docker', 'veth', 'eno', 'em']
    with pyroute2.NDB() as ndb:
        for key in ndb.interfaces:
            if not any(ifname in key['ifname'] for ifname in ded_ifs):
                interfaces.append(key['ifname'])

    parser = argparse.ArgumentParser()
    parser.add_argument('interface', type=str, choices=interfaces, nargs='+', help='Interface to tune')
    args = parser.parse_args()
    main(args.interface)
