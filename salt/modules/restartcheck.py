# -*- coding: utf-8 -*-
'''
checkrestart functionality for Debian and Red Hat Based systems

Identifies services (processes) that are linked against deleted files (for example after downloading an updated
binary of a shared library).

Based on checkrestart script from debian-goodies (written  by Matt Zimmerman for the Debian GNU/Linux distribution,
https://packages.debian.org/debian-goodies) and psdel by Sam Morris.

:codeauthor: Jiri Kotlin <jiri.kotlin@ultimum.io>
'''
from __future__ import absolute_import, unicode_literals, print_function

# Import python libs
import os
import re
import subprocess
import sys

# Import salt libs
import salt.utils.files
import salt.utils.path

# Import 3rd partylibs
from salt.ext import six

HAS_PSUTIL = False
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    pass


def __virtual__():
    '''
    Only run this module if the psutil python module is installed (package python-psutil).
    '''
    return HAS_PSUTIL


def _valid_deleted_file(path):
    '''
    Filters file path against unwanted directories and decides whether file is marked as deleted.

    Returns:
        True if file is desired deleted file, else False.

    Args:
        path: A string - path to file
    '''
    ret = False
    if path.endswith(' (deleted)'):
        ret = True
    if re.compile(r"\(path inode=[0-9]+\)$").search(path):
        ret = True
    # We don't care about log files
    if path.startswith('/var/log/') or path.startswith('/var/local/log/'):
        ret = False
    # Or about files under temporary locations
    if path.startswith('/var/run/') or path.startswith('/var/local/run/'):
        ret = False
    # Or about files under /tmp
    if path.startswith('/tmp/'):
        ret = False
    # Or about files under /dev/shm
    if path.startswith('/dev/shm/'):
        ret = False
    # Or about files under /run
    if path.startswith('/run/'):
        ret = False
    # Or about files under /drm
    if path.startswith('/drm'):
        ret = False
    # Or about files under /var/tmp and /var/local/tmp
    if path.startswith('/var/tmp/') or path.startswith('/var/local/tmp/'):
        ret = False
    # Or /dev/zero
    if path.startswith('/dev/zero'):
        ret = False
    # Or /dev/pts (used by gpm)
    if path.startswith('/dev/pts/'):
        ret = False
    # Or /usr/lib/locale
    if path.startswith('/usr/lib/locale/'):
        ret = False
    # Skip files from the user's home directories
    # many processes hold temporafy files there
    if path.startswith('/home/'):
        ret = False
    # Skip automatically generated files
    if path.endswith('icon-theme.cache'):
        ret = False
    # Skip font files
    if path.startswith('/var/cache/fontconfig/'):
        ret = False
    # Skip Nagios Spool
    if path.startswith('/var/lib/nagios3/spool/'):
        ret = False
    # Skip nagios spool files
    if path.startswith('/var/lib/nagios3/spool/checkresults/'):
        ret = False
    # Skip Postgresql files
    if path.startswith('/var/lib/postgresql/'):
        ret = False
    # Skip VDR lib files
    if path.startswith('/var/lib/vdr/'):
        ret = False
    # Skip Aio files found in MySQL servers
    if path.startswith('/[aio]'):
        ret = False
    # ignore files under /SYSV
    if path.startswith('/SYSV'):
        ret = False
    return ret


def _deleted_files():
    '''
    Iterates over /proc/PID/maps and /proc/PID/fd links and returns list of desired deleted files.

    Returns:
        List of deleted files to analyze, False on failure.

    '''
    deleted_files = []

    for proc in psutil.process_iter():
        try:
            pinfo = proc.as_dict(attrs=['pid', 'name'])
            try:
                maps = salt.utils.files.fopen('/proc/{0}/maps'.format(pinfo['pid']))  # pylint: disable=resource-leakage
                dirpath = '/proc/' + six.text_type(pinfo['pid']) + '/fd/'
                listdir = os.listdir(dirpath)
            except (OSError, IOError):
                return False

            # /proc/PID/maps
            maplines = maps.readlines()
            maps.close()
            mapline = re.compile(r'^[\da-f]+-[\da-f]+ [r-][w-][x-][sp-] '
                                 r'[\da-f]+ [\da-f]{2}:[\da-f]{2} (\d+) *(.+)( \(deleted\))?\n$')

            for line in maplines:
                line = salt.utils.stringutils.to_unicode(line)
                matched = mapline.match(line)
                if matched:
                    path = matched.group(2)
                    if path:
                        if _valid_deleted_file(path):
                            val = (pinfo['name'], pinfo['pid'], path[0:-10])
                            if val not in deleted_files:
                                deleted_files.append(val)

            # /proc/PID/fd
            try:
                for link in listdir:
                    path = dirpath + link
                    readlink = os.readlink(path)
                    filenames = []

                    if os.path.isfile(readlink):
                        filenames.append(readlink)
                    elif os.path.isdir(readlink) and readlink != '/':
                        for root, dummy_dirs, files in salt.utils.path.os_walk(readlink, followlinks=True):
                            for name in files:
                                filenames.append(os.path.join(root, name))

                    for filename in filenames:
                        if _valid_deleted_file(filename):
                            val = (pinfo['name'], pinfo['pid'], filename)
                            if val not in deleted_files:
                                deleted_files.append(val)
            except OSError:
                pass

        except psutil.NoSuchProcess:
            pass

    return deleted_files


def _format_output(kernel_restart, packages, verbose, restartable, nonrestartable, restartservicecommands,
                   restartinitcommands):
    '''
    Formats the output of the restartcheck module.

    Returns:
        String - formatted output.

    Args:
        kernel_restart: indicates that newer kernel is instaled
        packages: list of packages that should be restarted
        verbose: enables extensive output
        restartable: list of restartable packages
        nonrestartable: list of non-restartable packages
        restartservicecommands: list of commands to restart services
        restartinitcommands: list of commands to restart init.d scripts

    '''
    if not verbose:
        packages = restartable + nonrestartable
        if kernel_restart:
            packages.append('System restart required.')
        return packages
    else:
        ret = ''
        if kernel_restart:
            ret = 'System restart required.\n\n'

        if packages:
            ret += "Found {0} processes using old versions of upgraded files.\n".format(len(packages))
            ret += "These are the packages:\n"

        if len(restartable) > 0:
            ret += "Of these, {0} seem to contain systemd service definitions or init scripts " \
                   "which can be used to restart them:\n".format(len(restartable))
            for package in restartable:
                ret += package + ':\n'
                for program in packages[package]['processes']:
                    ret += program + '\n'

            if len(restartservicecommands) > 0:
                ret += "\n\nThese are the systemd services:\n"
                ret += '\n'.join(restartservicecommands)

            if len(restartinitcommands) > 0:
                ret += "\n\nThese are the initd scripts:\n"
                ret += '\n'.join(restartinitcommands)

        if len(nonrestartable) > 0:
            ret += "\n\nThese processes {0} do not seem to have an associated init script " \
                   "to restart them:\n".format(len(nonrestartable))
            for package in nonrestartable:
                ret += package + ':\n'
                for program in packages[package]['processes']:
                    ret += program + '\n'
    return ret


def _kernel_versions_debian():
    '''
    Last installed kernel name, for Debian based systems.

    Returns:
            List with possible names of last installed kernel
            as they are probably interpreted in output of `uname -a` command.
    '''
    kernel_get_selections = __salt__['cmd.run']('dpkg --get-selections linux-image-*')
    kernels = []
    kernel_versions = []
    for line in kernel_get_selections.splitlines():
        kernels.append(line)

    try:
        kernel = kernels[-2]
    except IndexError:
        kernel = kernels[0]

    kernel = kernel.rstrip('\t\tinstall')

    kernel_get_version = __salt__['cmd.run']('apt-cache policy ' + kernel)

    for line in kernel_get_version.splitlines():
        if line.startswith('  Installed: '):
            kernel_v = line.strip('  Installed: ')
            kernel_versions.append(kernel_v)
            break

    if __grains__['os'] == 'Ubuntu':
        kernel_v = kernel_versions[0].rsplit('.', 1)
        kernel_ubuntu_generic = kernel_v[0] + '-generic #' + kernel_v[1]
        kernel_ubuntu_lowlatency = kernel_v[0] + '-lowlatency #' + kernel_v[1]
        kernel_versions.extend([kernel_ubuntu_generic, kernel_ubuntu_lowlatency])

    return kernel_versions


def _kernel_versions_redhat():
    '''
    Name of the last installed kernel, for Red Hat based systems.

    Returns:
            List with name of last installed kernel as it is interpreted in output of `uname -a` command.
    '''
    kernel_get_last = __salt__['cmd.run']('rpm -q --last kernel')
    kernels = []
    kernel_versions = []
    for line in kernel_get_last.splitlines():
        if 'kernel-' in line:
            kernels.append(line)

    kernel = kernels[0].split(' ', 1)[0]
    kernel = kernel.strip('kernel-')
    kernel_versions.append(kernel)

    return kernel_versions


def _is_older_nilrt():
    '''
    If this is an older version of NILinuxRT, return True. Otherwise, return False.
    '''
    return bool(os.path.exists('/usr/local/natinst/bin/nisafemodeversion'))


def _kernel_versions_nilrt():
    '''
    Last installed kernel name, for Debian based systems.

    Returns:
            List with possible names of last installed kernel
            as they are probably interpreted in output of `uname -a` command.
    '''
    kernel_versions = []
    kvregex = r'[0-9]+\.[0-9]+\.[0-9]+-rt'
    kv_cmd = 'strings {0} | awk \'$1 ~ /{1}/ {{print $1}}\' | head -n1'

    if _is_older_nilrt():
        if 'arm' in __grains__.get('cpuarch'):
            # the kernel is inside a uboot created itb (FIT) image alongside the
            # device tree, ramdisk and a bootscript. There is no package management
            # or any other kind of versioning info, so we need to extract the itb.
            itb_path = '/boot/linux_runmode.itb'
            compressed_kernel = '/var/volatile/tmp/uImage.gz'
            uncompressed_kernel = '/var/volatile/tmp/uImage'
            __salt__['cmd.run']('dumpimage -i {0} -T flat_dt -p0 kernel -o {1}'
                                .format(itb_path, compressed_kernel))
            __salt__['cmd.run']('gunzip {0}'.format(compressed_kernel))
            kernel = __salt__['cmd.shell'](kv_cmd.format(uncompressed_kernel, kvregex))
        else:
            # the kernel bzImage is copied to rootfs without package management or
            # other versioning info.
            kpath = '/boot/runmode/bzImage'
            kernel = __salt__['cmd.shell'](kv_cmd.format(kpath, kvregex))
    else:
        # kernels in newer NILRT's are installed via package management and
        # have the version appended in the kernel image name
        if 'arm' in __grains__.get('cpuarch'):
            kernel = os.path.basename(os.readlink('/boot/uImage')).strip('uImage-')
        else:
            kernel = os.path.basename(os.readlink('/boot/bzImage')).strip('bzImage-')

    kernel_versions.append(kernel)

    return kernel_versions


def _file_changed_nilrt(full_filepath):
    '''
    Detect whether a file changed in an NILinuxRT system using md5sum and timestamp
    files from a state directory.

    Returns:
             - False if md5sum/timestamp state files don't exist
             - True/False depending if ``base_filename`` got modified/touched
    '''
    rs_state_dir = "/var/lib/salt/restartcheck_state"
    base_filename = os.path.basename(full_filepath)
    timestamp_file = os.path.join(rs_state_dir, '{0}.timestamp'.format(base_filename))
    md5sum_file = os.path.join(rs_state_dir, '{0}.md5sum'.format(base_filename))

    if not os.path.exists(timestamp_file) or not os.path.exists(md5sum_file):
        return True

    prev_timestamp = __salt__['file.read'](timestamp_file).rstrip()
    # Need timestamp in seconds so floor it using int()
    cur_timestamp = str(int(os.path.getmtime(full_filepath)))

    if prev_timestamp != cur_timestamp:
        return True

    return bool(__salt__['cmd.retcode']('md5sum -cs {0}'.format(md5sum_file), output_loglevel="quiet"))


def _kernel_modules_changed_nilrt(kernelversion):
    '''
    Once a NILRT kernel module is inserted, it can't be rmmod so systems need
    rebooting (some modules explicitly ask for reboots even on first install),
    hence this functionality of determining if the module state got modified by
    testing if depmod was run.

    Returns:
             - True/False depending if modules.dep got modified/touched
    '''
    if kernelversion is not None:
        return _file_changed_nilrt('/lib/modules/{0}/modules.dep'.format(kernelversion))
    return False


def _sysapi_changed_nilrt():
    '''
    Besides the normal Linux kernel driver interfaces, NILinuxRT-supported hardware features an
    extensible, plugin-based device enumeration and configuration interface named "System API".
    When an installed package is extending the API it is very hard to know all repercurssions and
    actions to be taken, so reboot making sure all drivers are reloaded, hardware reinitialized,
    daemons restarted, etc.

    Returns:
             - True/False depending if nisysapi .ini files got modified/touched
             - False if no nisysapi .ini files exist
    '''
    nisysapi_path = '/usr/local/natinst/share/nisysapi.ini'
    if os.path.exists(nisysapi_path) and _file_changed_nilrt(nisysapi_path):
        return True

    restartcheck_state_dir = '/var/lib/salt/restartcheck_state'
    nisysapi_conf_d_path = "/usr/lib/{0}/nisysapi/conf.d/experts/".format(
        'arm-linux-gnueabi' if 'arm' in __grains__.get('cpuarch') else 'x86_64-linux-gnu'
    )

    if os.path.exists(nisysapi_conf_d_path):
        rs_count_file = '{0}/sysapi.conf.d.count'.format(restartcheck_state_dir)
        if not os.path.exists(rs_count_file):
            return True

        with salt.utils.files.fopen(rs_count_file, 'r') as fcount:
            current_nb_files = len(os.listdir(nisysapi_conf_d_path))
            rs_stored_nb_files = int(fcount.read())
            if current_nb_files != rs_stored_nb_files:
                return True

        for fexpert in os.listdir(nisysapi_conf_d_path):
            if _file_changed_nilrt('{0}/{1}'.format(nisysapi_conf_d_path, fexpert)):
                return True

    return False


def restartcheck(ignorelist=None, blacklist=None, excludepid=None, verbose=True):
    '''
    Analyzes files openeded by running processes and seeks for packages which need to be restarted.

    Args:
        ignorelist: string or list of packages to be ignored
        blacklist: string or list of file paths to be ignored
        excludepid: string or list of process IDs to be ignored
        verbose: boolean, enables extensive output

    Returns:
        Dict on error: { 'result': False, 'comment': '<reason>' }
        String with checkrestart output if some package seems to need to be restarted or
        if no packages need restarting.

    .. versionadded:: 2015.8.3

    CLI Example:
    .. code-block:: bash

        salt '*' restartcheck.restartcheck
    '''
    kernel_restart = True
    if __grains__.get('os_family') == 'Debian':
        cmd_pkg_query = 'dpkg-query --listfiles '
        systemd_folder = '/lib/systemd/system/'
        systemd = '/bin/systemd'
        kernel_versions = _kernel_versions_debian()
    elif __grains__.get('os_family') == 'RedHat':
        cmd_pkg_query = 'repoquery -l '
        systemd_folder = '/usr/lib/systemd/system/'
        systemd = '/usr/bin/systemctl'
        kernel_versions = _kernel_versions_redhat()
    elif __grains__.get('os_family') == 'NILinuxRT':
        cmd_pkg_query = 'opkg files '
        systemd = ''
        kernel_versions = _kernel_versions_nilrt()
    else:
        return {'result': False, 'comment': 'Only available on Debian, Red Hat and NI Linux Real-Time based systems.'}

    # Check kernel versions
    kernel_current = __salt__['cmd.run']('uname -a')
    for kernel in kernel_versions:
        if kernel in kernel_current:
            if __grains__.get('os_family') == 'NILinuxRT':
                # Check kernel modules and hardware API's for version changes
                # If a restartcheck=True event was previously witnessed, propagate it
                if not _kernel_modules_changed_nilrt(kernel) and \
                   not _sysapi_changed_nilrt() and \
                   not __salt__['system.get_reboot_required_witnessed']():
                    kernel_restart = False
                    break
            else:
                kernel_restart = False
                break

    packages = {}

    if ignorelist:
        if not isinstance(ignorelist, list):
            ignorelist = [ignorelist]
    else:
        ignorelist = ['screen', 'systemd']

    if blacklist:
        if not isinstance(blacklist, list):
            blacklist = [blacklist]
    else:
        blacklist = []

    if excludepid:
        if not isinstance(excludepid, list):
            excludepid = [excludepid]
    else:
        excludepid = []

    deleted_files = _deleted_files()

    if not isinstance(deleted_files, list):
        return {'result': False, 'comment': 'Could not get list of processes. '
                                            '(Do you have root access?)'}

    owners_cache = {}

    for deleted_file in deleted_files:
        name, pid, path = deleted_file[0], deleted_file[1], deleted_file[2]
        if path in blacklist or pid in excludepid:
            continue
        try:
            readlink = os.readlink('/proc/{0}/exe'.format(pid))
        except OSError:
            excludepid.append(pid)
            continue
        try:
            packagename = owners_cache[readlink]
        except KeyError:
            packagename = __salt__['pkg.owner'](readlink)
            if not packagename:
                packagename = name
            owners_cache[readlink] = packagename
        if packagename and packagename not in ignorelist:
            program = '\t' + six.text_type(pid) + ' ' + readlink + ' (file: ' + six.text_type(path) + ')'
            if packagename not in packages:
                packages[packagename] = {'initscripts': [], 'systemdservice': [], 'processes': [program],
                                         'process_name': name}
            else:
                if program not in packages[packagename]['processes']:
                    packages[packagename]['processes'].append(program)

    if len(packages) == 0 and not kernel_restart:
        return 'No packages seem to need to be restarted.'

    for package in packages:
        cmd = cmd_pkg_query + package
        paths = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE)

        while True:
            line = salt.utils.stringutils.to_unicode(paths.stdout.readline())
            if not line:
                break
            pth = line[:-1]
            if pth.startswith('/etc/init.d/') and not pth.endswith('.sh'):
                packages[package]['initscripts'].append(pth[12:])

            if os.path.exists(systemd) and pth.startswith(systemd_folder) and pth.endswith('.service') and \
               pth.find('.wants') == -1:
                is_oneshot = False
                try:
                    servicefile = salt.utils.files.fopen(pth)  # pylint: disable=resource-leakage
                except IOError:
                    continue
                sysfold_len = len(systemd_folder)

                for line in servicefile.readlines():
                    line = salt.utils.stringutils.to_unicode(line)
                    if line.find('Type=oneshot') > 0:
                        # scripts that does a single job and then exit
                        is_oneshot = True
                    continue
                servicefile.close()

                if not is_oneshot:
                    packages[package]['systemdservice'].append(pth[sysfold_len:])

            sys.stdout.flush()
        paths.stdout.close()

    # Alternatively, find init.d script or service that match the process name
    for package in packages:
        if len(packages[package]['systemdservice']) == 0 and len(packages[package]['initscripts']) == 0:
            service = __salt__['service.available'](packages[package]['process_name'])

            if service:
                if os.path.exists('/etc/init.d/' + packages[package]['process_name']):
                    packages[package]['initscripts'].append(packages[package]['process_name'])
                else:
                    packages[package]['systemdservice'].append(packages[package]['process_name'])

    restartable = []
    nonrestartable = []
    restartinitcommands = []
    restartservicecommands = []

    for package in packages:
        if len(packages[package]['initscripts']) > 0:
            restartable.append(package)
            restartinitcommands.extend(['service ' + s + ' restart' for s in packages[package]['initscripts']])
        elif len(packages[package]['systemdservice']) > 0:
            restartable.append(package)
            restartservicecommands.extend(['systemctl restart ' + s for s in packages[package]['systemdservice']])
        else:
            nonrestartable.append(package)

    ret = _format_output(kernel_restart, packages, verbose, restartable, nonrestartable,
                         restartservicecommands, restartinitcommands)
    return ret
