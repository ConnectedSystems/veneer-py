from subprocess import Popen, PIPE
from queue import Queue, Empty  # python 3.x
from threading  import Thread
import sys
from time import sleep
from glob import glob
import atexit
import os
import tempfile
import shutil

# Non blocking IO solution from http://stackoverflow.com/a/4896288
ON_POSIX = 'posix' in sys.builtin_module_names

VENEER_EXE_FN='FlowMatters.Source.VeneerCmd.exe'
MANY_VENEERS='D:\\src\\projects\\Veneer\\Compiled'
VENEER_EXE='D:\\src\\projects\\Veneer\\Output\\FlowMatters.Source.VeneerCmd.exe'


def kill_all_on_exit(processes):
    def end_processes():
        for p in processes:
            p.kill()
    atexit.register(end_processes)

def kill_all_now(processes):
    for p in processes:
        p.kill()

def _enqueue_output(out, queue):
    for line in iter(out.readline, b''):
        queue.put(line)
    out.close()

def find_veneer_cmd_line_exe(project_fn=None,source_version=None):
    if project_fn:
        project_dir = os.path.dirname(project_fn)
        print('Looking for source_version.txt in %s'%project_dir)
        ver_file = os.path.join(project_dir,'source_version.txt')
        if os.path.exists(ver_file):
            version = open(ver_file).read().split('\n')[0]
            print('Trying to interpret source version=%s'%version)
            if os.path.exists(version): # Actual path to Source...
                direct_path = os.path.join(version,VENEER_EXE_FN)
                print('Trying to go directly to %s'%direct_path)
                if os.path.exists(direct_path):
                    return direct_path

            version_in_many = glob(os.path.join(MANY_VENEERS,'*'+version+'*'))
            if len(version_in_many):
                path_in_many = os.path.join(version_in_many[0],VENEER_EXE_FN)
                print('Trying for %s'%path_in_many)
                if os.path.exists(path_in_many):
                    return path_in_many
    return VENEER_EXE

def create_command_line(veneer_path,source_version="4.1.1",source_path='C:\\Program Files\\eWater',dest=None):
    '''
    Copy all Veneer related files and all files from the relevant Source distribution to a third directory,
    for use as the veneer command line.

    veneer_path: Directory containing the Veneer files

    source_version: Version of Source to locate and copy

    source_path: Base installation directory for eWater Source

    dest: Destination to copy Source and Veneer to. If not provided, a temporary directory will be created.

    Returns: Full path to FlowMatters.Source.VeneerCmd.exe for use with start()

    Note: It is your responsibility to delete the copy of Source and Veneer from the destination directory
    when you are finished with it! (Even if a temporary directory is used!)
    '''
    if dest is None:
        dest = tempfile.mkdtemp(suffix='_veneer')

    if not os.path.exists(dest):
        os.makedirs(dest)

    available = glob(os.path.join(source_path,'Source *'))
    versions = [os.path.basename(ver).split(' ')[1] for ver in available]
    chosen_one = [ver for ver in versions if ver.startswith(source_version)][0]
    chosen_one_full = [product_ver for product_ver in available if chosen_one in product_ver][0]

    for f in glob(os.path.join(source_path,chosen_one_full,'*.*')):
        if not os.path.isfile(f):
            continue
        shutil.copyfile(f,os.path.join(dest,os.path.basename(f)))

    for f in glob(os.path.join(veneer_path,'*.*')):
        if not os.path.isfile(f):
            continue
        shutil.copyfile(f,os.path.join(dest,os.path.basename(f)))

    exe_path = os.path.join(dest,'FlowMatters.Source.VeneerCmd.exe')
    assert os.path.exists(exe_path)
    return exe_path

def clean_up_cmd_line_exe(path=None):
    if path is None:
        pass

    shutil.rmtree(path)

def configure_non_blocking_io(processes,stream):
    queues = [Queue() for p in range(len(processes))]
    threads = [Thread(target=_enqueue_output,args=(getattr(p,stream),q)) for (p,q) in zip(processes,queues)]
    for t in threads:
        t.daemon = True
        t.start()
    return queues,threads

def start(project_fn,n_instances=1,ports=9876,debug=False,remote=True,script=True, veneer_exe=None):
    """
    Start one or more copies of the Veneer command line progeram with a given project file

    Parameters:

    - project_fn - Path to a Source project file (.rsproj)

    - n_instances - Number of copies of the Veneer command line to start (default: 1)

    - ports - A single port number, indicating the port number of the first copy of the Veneer command line,
              OR a list of ports, in which case len(ports)==n_instances  (default: 9876)

    - debug - Set to True to echo all output from Veneer Command Line during startup

    - remote - Allow remote connections (requires registration)

    - script - Allow IronPython scripts

    - veneer_exe - Optional (but often required) path to the Veneer Command Line. If not provided,
                   veneer-py will attempt to identify the version of Veneer Command Line to invoke.
                   If there is a source_version.txt file in the same directory as the project file,
                   this text file will be consulted to identify the version of Source.

    returns processes, ports
       processes - list of process objects that can be used to terminate the servers
       ports - the port numbers used for each copy of the server
    """
    if not hasattr(ports,'__len__'):
        ports = list(range(ports,ports+n_instances))

    if not veneer_exe:
        veneer_exe = find_veneer_cmd_line_exe(project_fn)

    project_fn = os.path.abspath(project_fn)
    extras = ''
    if remote: extras += '-r '
    if script: extras += '-s '

    cmd_line = '%s -p %%d %s "%s"'%(veneer_exe,extras,project_fn)
    cmd_lines = [cmd_line%port for port in ports]
    if debug:
        for cmd in cmd_lines:
            print('Starting %s'%cmd)
    processes = [Popen(cmd_line%port,stdout=PIPE,stderr=PIPE,bufsize=1, close_fds=ON_POSIX) for port in ports]
    std_out_queues,std_out_threads = configure_non_blocking_io(processes,'stdout')
    std_err_queues,std_err_threads = configure_non_blocking_io(processes,'stderr')

    ready = [False for p in range(n_instances)]
    failed = [False for p in range(n_instances)]

    all_ready = False
    any_failed = False
    actual_ports = ports[:]
    while not (all_ready or any_failed):
        for i in range(n_instances):
            end=False
            while not end:
                try:
                    line = std_err_queues[i].get_nowait().decode('utf-8')
                except Empty:
                    end = True
                    pass
                else:
                    print('ERROR[%d] %s'%(i,line))

            if ready[i]:
                continue

            end = False
            while not end:
                try:
                    line = std_out_queues[i].get_nowait().decode('utf-8')
                except Empty:
                    end = True
                    sleep(0.05)
    #               print('.')
    #               sys.stdout.flush()
                else:
                    if line.startswith('Started Source RESTful Service on port'):
                        actual_ports[i] = int(line.split(':')[-1])

                    if line.startswith('Server started. Ctrl-C to exit'):
                        ready[i] = True
                        end = True
                        print('Server %d on port %d is ready'%(i,ports[i]))
                        sys.stdout.flush()
                    elif line.startswith('Cannot find project') or line.startswith('Unhandled exception'):
                        end = True
                        failed[i] = True
                        print('Server %d on port %d failed to start'%(i,ports[i]))
                        sys.stdout.flush()
                    if debug and not line.startswith('Unable to delete'):
                        print("[%d] %s"%(i,line))
                        sys.stdout.flush()
        all_ready = len([r for r in ready if not r])==0
        any_failed = len([f for f in failed if f])>0

    if any_failed:
        for p in processes:
            p.kill()
        raise Exception('One or more instances of Veneer failed to start. Try again with debug=True to see output')

    kill_all_on_exit(processes)
    return processes,actual_ports


