from __future__ import annotations
from argparse import ArgumentParser, HelpFormatter, Namespace
from concurrent.futures import ThreadPoolExecutor
from getpass import getpass
from typing import Any, Callable, Generator

import itertools
import logging
import os
import shutil
import sys

from mssql_spider import log
from mssql_spider.client import MSSQLClient
from mssql_spider.modules import clrexec, coerce, dump, exec, fs, query, reg, sysinfo

HEADER = '\n'.join((
    r'                              __                 _     __',
    r'   ____ ___  ______________ _/ /     _________  (_)___/ /__  _____',
    r'  / __ `__ \/ ___/ ___/ __ `/ /_____/ ___/ __ \/ / __  / _ \/ ___/',
    r' / / / / / (__  |__  ) /_/ / /_____(__  ) /_/ / / /_/ /  __/ /',
    r'/_/ /_/ /_/____/____/\__, /_/     /____/ .___/_/\__,_/\___/_/',
    r'                       /_/            /_/',
    r'',
    r'legend: => linked instance, -> impersonated user/login',
    r'',
))


def main() -> None:
    entrypoint = ArgumentParser(formatter_class=lambda prog: HelpFormatter(prog, max_help_position=round(shutil.get_terminal_size().columns / 2)))  # scale width of help text with terminal width

    entrypoint.add_argument('--depth', type=int, default=10, metavar='UINT', help='default: 10')
    entrypoint.add_argument('--threads', type=int, default=min((os.cpu_count() or 1) * 4, 16), metavar='UINT', help='default: based on CPU cores')
    entrypoint.add_argument('--timeout', type=int, default=5, metavar='SECONDS', help='default: 5')
    entrypoint.add_argument('--debug', action='store_true', help='write verbose log to stderr')

    auth = entrypoint.add_argument_group('authentication')
    auth.add_argument('-d', '--domain', default='', metavar='DOMAIN', help='implies -w')
    auth.add_argument('-u', '--user', metavar='USERNAME')

    authsecret = auth.add_mutually_exclusive_group()
    authsecret.add_argument('-p', '--password', metavar='PASSWORD', default='')
    authsecret.add_argument('-n', '--no-pass', action='store_true', help='disable password prompt, default: false')
    authsecret.add_argument('-H', '--hashes', metavar='[LMHASH:]NTHASH', help='authenticate via pass the hash')
    authsecret.add_argument('-a', '--aes-key', metavar='HEXKEY', help='authenticate with Kerberos key in hex, implies -k')

    auth.add_argument('-w', '--windows-auth', action='store_true', help='use windows instead of local authentication, default: false')
    auth.add_argument('-k', '--kerberos', action='store_true', help='authenticate via Kerberos, implies -w, default: false')
    auth.add_argument('-K', '--dc-ip', metavar='ADDRESS', help='FQDN or IP address of a domain controller, default: value of -d')
    auth.add_argument('-D', '--database', metavar='NAME')

    enumeration = entrypoint.add_argument_group('enumeration')
    enumeration.add_argument('-q', '--query', action='append', metavar='SQL', help='execute SQL statement, unprivileged, repeatable')
    enumeration.add_argument('--sysinfo', action='store_true', help='retrieve database and OS version, unprivileged')
    #enumeration.add_argument('--databases', action='store_true', help='unprivileged')
    #enumeration.add_argument('--tables', action='store_true', help='unprivileged')
    #enumeration.add_argument('--columns', action='store_true', help='unprivileged')

    coercion = entrypoint.add_argument_group('coercion')
    coercion.add_argument('-c', '--coerce-dirtree', dest='coerce_dirtree', action='append', metavar='UNCPATH', help='coerce NTLM trough xp_dirtree(), unprivileged')
    coercion.add_argument('--coerce-fileexist', action='append', metavar='UNCPATH', help='coerce NTLM trough xp_fileexist(), unprivileged')
    coercion.add_argument('--coerce-openrowset', action='append', metavar='UNCPATH', help='coerce NTLM trough openrowset(), privileged')

    fs = entrypoint.add_argument_group('filesystem')
    fs.add_argument('--file-read', action='append', metavar='REMOTE', help='read file trough openrowset(), privileged')
    fs.add_argument('--file-write', nargs=2, action='append', metavar=('LOCAL', 'REMOTE'), help='write file trough OLE automation, privileged')

    exec = entrypoint.add_argument_group('execution')
    exec.add_argument('-x', '--exec-cmdshell', action='append', metavar='COMMAND', help='execute command trough xp_cmdshell(), privileged')
    exec.add_argument('--exec-ole', action='append', metavar='COMMAND', help='execute blind command trough OLE automation, privileged')
    exec.add_argument('--exec-job', nargs=2, action='append', metavar=('sql|cmd|powershell|jscript|vbscript', 'COMMAND'), help='execute blind command trough agent job, privileged, experimental!')
    exec.add_argument('--exec-clr', nargs='+', action='append', metavar=('ASSEMBLY FUNCTION', 'ARGS'), help='execute .NET DLL, privileged')

    reg = entrypoint.add_argument_group('registry')
    reg.add_argument('--reg-read', nargs=3, action='append', metavar=('HIVE', 'KEY', 'NAME'), help='read registry value, privileged, experimental!')
    reg.add_argument('--reg-write', nargs=5, action='append', metavar=('HIVE', 'KEY', 'NAME', 'TYPE', 'VALUE'), help='write registry value, privileged, experimental!')
    reg.add_argument('--reg-delete', nargs=3, action='append', metavar=('HIVE', 'KEY', 'NAME'), help='delete registry value, privileged, experimental!')

    creds = entrypoint.add_argument_group('credentials')
    creds.add_argument('--dump-hashes', action='store_true', help='extract hashes of database logins, privileged')
    creds.add_argument('--dump-jobs', action='store_true', help='extract source code of agent jobs, privileged')
    creds.add_argument('--dump-autologon', action='store_true', help='extract autologon credentials from registry, privileged')

    entrypoint.add_argument('targets', nargs='+', metavar='HOST[:PORT]|FILE')

    opts = entrypoint.parse_args()

    if opts.exec_clr:
        if any(len(argset) < 2 for argset in opts.exec_clr):
            entrypoint.print_help()
            return

    if opts.debug:
        logging.basicConfig(level=logging.DEBUG, stream=sys.stderr, format='%(levelname)s:%(name)s:%(module)s:%(lineno)s:%(message)s')
        logging.getLogger('impacket').setLevel(logging.WARNING)
    else:
        logging.basicConfig(level=logging.FATAL, format='%(message)s')

    if not opts.password and not opts.hashes and not opts.no_pass and not opts.aes_key:
        opts.password = getpass('password: ')
    if opts.hashes and ':' not in opts.hashes:
        # format for impacket
        opts.hashes = f':{opts.hashes}'
    if opts.aes_key:
        opts.kerberos = True
    if opts.domain:
        opts.windows_auth = True

    print(HEADER)

    with ThreadPoolExecutor(max_workers=opts.threads) as pool:
        for _ in pool.map(_process_target, itertools.repeat(opts), _load_targets(opts.targets), itertools.repeat(opts.user), itertools.repeat(opts.password), itertools.repeat(opts.hashes), itertools.repeat(opts.aes_key)):
            continue


#def _load_files(items: list[str]) -> Generator[str, None, None]:
#    for item in items:
#        if os.path.isfile(item):
#            with open(item) as file:
#                for line in file:
#                    yield line
#        else:
#            yield item


def _load_targets(targets: list[str]) -> Generator[tuple[str, int], None, None]:
    for item in targets:
        if os.path.isfile(item):
            with open(item) as file:
                for line in file:
                    yield _parse_target(line)
        else:
            yield _parse_target(item)


def _parse_target(value: str) -> tuple[str, int]:
    value = value.strip()
    parts = value.rsplit(':', maxsplit=1)
    if len(parts) == 1:
        return value, 1433
    else:
        return parts[0], int(parts[1])


def _process_target(opts: Namespace, target: tuple[str, int], user: str, password: str, hashes: str, aes_key: str) -> None:
    try:
        client = MSSQLClient.connect(target[0], target[1], timeout=opts.timeout)
    except Exception as e:
        log.general_error(target, 'connection', e)
        logging.exception(e)
        return

    try:
        client.login(
            domain=opts.domain,
            username=user,
            password=password,
            hashes=hashes,
            aes_key=aes_key,
            windows_auth=opts.windows_auth,
            kerberos=opts.kerberos,
            kdc_host=opts.dc_ip,
            database=opts.database,
        )
    except (Exception, OSError) as e:
        log.general_error(target, 'authentication', e)
        logging.exception(e)
        return

    try:
        client.spider(lambda c: _visitor(opts, c), max_depth=opts.depth)
    except TimeoutError as e:
        log.general_error(target, 'connection', e, hint=f'retry with --timeout {opts.timeout * 3}')
        logging.exception(e)
    except Exception as e:
        log.general_error(target, 'connection', e)
        logging.exception(e)


def _visitor(opts: Namespace, client: MSSQLClient) -> None:
    if opts.query:
        _try_visitor(client, 'query', query.run, opts.query)
    if opts.sysinfo:
        _try_visitor_single(client, 'sysinfo', sysinfo.run, [])
    if opts.coerce_dirtree:
        _try_visitor(client, 'coerce-dirtee', coerce.dirtree, opts.coerce_dirtree)
    if opts.coerce_fileexist:
        _try_visitor(client, 'coerce-fileexist', coerce.fileexist, opts.coerce_fileexist)
    if opts.coerce_openrowset:
        _try_visitor(client, 'coerce-openrowset', coerce.openrowset, opts.coerce_openrowset)
    if opts.file_read:
        _try_visitor(client, 'fs-read', fs.read, opts.file_read)
    if opts.file_write:
        _try_visitor(client, 'fs-write', fs.write, opts.file_write)
    if opts.exec_cmdshell:
        _try_visitor(client, 'exec-cmdshell', exec.cmdshell, opts.exec_cmdshell)
    if opts.exec_ole:
        _try_visitor(client, 'exec-ole', exec.ole, opts.exec_ole)
    if opts.exec_job:
        _try_visitor(client, 'exec-job', exec.job, opts.exec_job)
    if opts.exec_clr:
        _try_visitor(client, 'exec-clr', clrexec.clrexec, opts.exec_clr)
    if opts.reg_read:
        _try_visitor(client, 'reg-read', reg.read, opts.reg_read)
    if opts.reg_write:
        _try_visitor(client, 'reg-write', reg.write, opts.reg_write)
    if opts.reg_delete:
        _try_visitor(client, 'reg-delete', reg.delete, opts.reg_delete)
    if opts.dump_hashes:
        _try_visitor_single(client, 'dump-hashes', dump.hashes, [])
    if opts.dump_jobs:
        _try_visitor_single(client, 'dump-jobs', dump.jobs, [])
    if opts.dump_autologon:
        _try_visitor_single(client, 'dump-autologon', dump.autologon, [])


def _try_visitor(client: MSSQLClient, name: str, function: Callable, items: list[list[Any]]) -> None:
    for args in items:
        _try_visitor_single(client, name, function, args)


def _try_visitor_single(client: MSSQLClient, name: str, function: Callable, args: str|list[str]) -> None:
    try:
        if isinstance(args, list):
            result = function(client, *args)
        else:
            result = function(client, args)
        log.module_result(client, name, result)
    except Exception as e:
        log.module_error(client, name, e)
        logging.exception(e)


if __name__ == '__main__':
    main()
