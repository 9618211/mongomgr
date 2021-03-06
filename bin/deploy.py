#!/usr/bin/env python
#coding: utf-8
#file   : deploy.py
#author : ning
#date   : 2012-08-24 06:26:25

import urllib, urllib2, httplib, os, re, sys, time, logging, hmac, base64, commands, glob
import json
import argparse
import socket

from pcl import common

PWD = os.path.dirname(os.path.realpath(__file__))
WORKDIR = os.path.join(PWD,  '../')
LOGPATH = os.path.join(WORKDIR, 'log/deploy.log')

sys.path.append(os.path.join(WORKDIR, 'lib/'))
sys.path.append(os.path.join(WORKDIR, 'conf/'))

import deploy_conf as conf

class TmpFile:
    def __init__(self, tmp_dir = './tmp/'):
        self.tmp_dir = tmp_dir

        if not os.path.exists(tmp_dir):
            os.makedirs(tmp_dir, mode=0777) 
            os.chmod(tmp_dir, 0777)

    def random_tmp_file(self, key):
        from datetime import datetime
        name = str(datetime.now())
        name = name.replace(' ', '_')
        name = name.replace(':', '_')
        name = name.replace('.', '_')
        return self.tmp_dir + key + name

    def content_to_tmpfile(self, content):
        tmp_file = self.random_tmp_file('_')
        f = file(tmp_file, 'wb')
        f.write(content)
        f.close()
        return tmp_file

def _remote_run(user, host, raw_cmd):
    if raw_cmd.find('"') >= 0:
        error('bad cmd: ' + raw_cmd)
        return
    cmd = 'ssh -n -f %s@%s "%s"' % (user, host, raw_cmd)
    return common.system(cmd, logging.info)

def _init():
    #common.system('rm -rf ./mongodb-base', logging.debug)

    common.system('mkdir -p ./mongodb-base/bin', logging.debug)
    common.system('mkdir -p ./mongodb-base/conf', logging.debug)
    common.system('mkdir -p ./mongodb-base/log', logging.debug)
    common.system('mkdir -p ./mongodb-base/db', logging.debug)

    common.system('cp -u %s/bin/mongo ./mongodb-base/bin' % conf.MONGO_DB_PATH, logging.debug)
    common.system('cp -u %s/bin/mongostat ./mongodb-base/bin' % conf.MONGO_DB_PATH, logging.debug)
    common.system('cp -u %s/bin/mongod ./mongodb-base/bin' % conf.MONGO_DB_PATH, logging.debug)
    common.system('cp -u %s/bin/mongos ./mongodb-base/bin' % conf.MONGO_DB_PATH, logging.debug)

    common.system('cp conf/mongod.conf ./mongodb-base/conf', logging.debug)

def _alive(mongod, auth=None):
    #logging.debug("alive %s %s" % (mongod, auth) )
    [host, port, path] = mongod
    cmd = 'mongostat --host %s --port %s -n1 ' % (host, port)
    if auth:
        tmp = '-u %s -p %s ' % ('__system', auth['key'])
        cmd += tmp

    r = common.system(cmd, logging.debug)
    if r.find('insert') >= 0:
        alive = True
    else:
        alive = False
    logging.info("%s alive = %s" % (mongod, alive))
    return alive
    
def _copy_files(mongod):
    [host, port, path] = mongod

    cmd = 'mkdir -p %s ' % path
    _remote_run(conf.USER, host, cmd)

    cmd = 'rsync -avP ./mongodb-base/ %s@%s:%s 1>/dev/null 2>/dev/null' % (conf.USER, host, path)
    common.system(cmd, logging.debug)

def _run_js(host, port, js, auth=None):
    logging.info('run_js: \n' + js.replace(' ', '').replace('\n', '  '))
    filename = TmpFile().content_to_tmpfile(js)

    cmd = './mongodb-base/bin/mongo %s:%d/admin ' % (host, port)
    if auth:
        tmp = '-u %s -p %s ' % ('__system', auth['key'])
        cmd += tmp
    cmd += filename

    rst = common.system(cmd, logging.info)
    if rst.find('command failed') >=0 or rst.find('uncaught exception') >=0:
        raise Exception('run js error: \n' + rst)
    logging.info(rst)

############# mongod op
def mongod_start(mongod, replset_name='', auth=None):
    [host, port, path] = mongod

    if _alive(mongod, auth):
        logging.info(' %s already alive:  we do nothing!' % mongod)
        return 
    cmd = 'cd %s && numactl --interleave=all ./bin/mongod -f ./conf/mongod.conf --port %d --fork ' % (path, port)

    if replset_name:
        tmp =  '--replSet %s ' % replset_name
        cmd += tmp
    if auth:
        common.system('echo "%s" > ./mongodb-base/conf/mongokey && chmod 700 ./mongodb-base/conf/mongokey' % auth['key'], logging.debug)
        tmp =  '--keyFile=%s/conf/mongokey ' % path
        cmd += tmp

    _copy_files(mongod)
    r = _remote_run(conf.USER, host, cmd)
    logging.debug(r)
    if r.find('forked process') == -1:
        raise Exception("%s mongod start Fail" % mongod)
    if not _alive(mongod, auth):
        logging.warning("%s start Fail" % mongod)

    logging.info("%s start Success" % mongod)

def mongod_ps(mongod):
    host, port, path = mongod
    cmd = 'pgrep -l -f \'./bin/mongod -f ./conf/mongod.conf --port %d \'' % (port, )
    print _remote_run(conf.USER, host, cmd)

def mongod_stop(mongod):
    host, port, path = mongod
    cmd = 'cd %s ; ./bin/mongod -f ./conf/mongod.conf --port %d --shutdown' % (path, port)
    print _remote_run(conf.USER, host, cmd)

def mongod_kill(mongod):
    host, port, path = mongod
    cmd = 'pkill -9 -f \'./bin/mongod -f ./conf/mongod.conf --port %d \'' % (port, )
    print _remote_run(conf.USER, host, cmd)

def mongod_log(mongod):
    [host, port, path] = mongod
    cmd = 'cd %s ; tail -20 log/mongod.log' % (path, )
    print _remote_run(conf.USER, host, cmd)

def mongod_clean(mongod):
    [host, port, path] = mongod
    cmd = 'rm -rf %s ' % (path)
    print _remote_run(conf.USER, host, cmd)


############# replset op
def replset_start(replset, auth):
    for mongod in  replset['mongod']:
        mongod_start(mongod, replset_name = replset['replset_name'], auth=auth)

    #make js
    time.sleep(5)
    members = [{'_id': id, 'host': '%s:%d'%(host,port) } for (id, (host, port, path)) in enumerate(replset['mongod'])]
    replset_config = {
        '_id': replset['replset_name'],
        'members': members
    }
    js = '''
config = %s;
rs.initiate(config);
''' % json.dumps(replset_config)
    if auth:
        tmp = 'db.addUser("%s", "%s");' % (auth['user'], auth['password'])
        #js += tmp
    
    primary = replset['mongod'][0]
    ip = socket.gethostbyname(primary[0])
    port = primary[1]
    _run_js(ip, port, js, auth)

    logging.info( 'see http://%s:%d/_replSet' % (primary[0], 1000+primary[1]) )


def replset_ps(replset):
    for host, port, path in  replset['mongod']:
        cmd = 'pgrep -l -f \'./bin/mongod -f ./conf/mongod.conf --port %d \'' % (port, )
        print _remote_run(conf.USER, host, cmd)

def replset_log(replset):
    for mongod in  replset['mongod']:
        mongod_log(mongod)

def replset_stop(replset):
    for mongod in  replset['mongod']:
        mongod_stop(mongod)

def replset_kill(replset):
    for mongod in  replset['mongod']:
        mongod_kill(mongod)

def replset_clean(replset):
    for mongod in  replset['mongod']:
        mongod_clean(mongod)

############# configserver op
def configserver_start(configserver, auth):
    [host, port, path] = configserver

    if _alive(configserver, auth):
        logging.info('%s already alive:  we do nothing!' % configserver)
        return 

    cmd = 'cd %s ; ./bin/mongod --configsvr --dbpath ./db --logpath ./log/mongod.log --port %d --fork ' % (path, port)
    if auth:
        common.system('echo "%s" > ./mongodb-base/conf/mongokey && chmod 700 ./mongodb-base/conf/mongokey' % auth['key'], logging.debug)
        tmp =  '--keyFile=%s/conf/mongokey ' % path
        cmd += tmp
    _copy_files(configserver)
    print _remote_run(conf.USER, host, cmd)

def configserver_ps(configserver):
    [host, port, path] = configserver

    cmd = '''pgrep -l -f './bin/mongod --configsvr --dbpath ./db --logpath ./log/mongod.log --port %d --fork' ''' % (port, )
    print _remote_run(conf.USER, host, cmd)

def configserver_kill(configserver):
    [host, port, path] = configserver

    cmd = '''pkill -f './bin/mongod --configsvr --dbpath ./db --logpath ./log/mongod.log --port %d --fork' ''' % (port, )
    print _remote_run(conf.USER, host, cmd)

############# mongos op
def mongos_start(mongos, configdb, auth):
    [host, port, path] = mongos

    if _alive(mongos, auth):
        logging.info('%s already alive:  we do nothing!' % mongos)
        return 

    cmd = 'cd %s ; numactl --interleave=all ./bin/mongos --configdb %s --logpath ./log/mongod.log --port %d --fork ' % (path, configdb, port)
    if auth:
        common.system('echo "%s" > ./mongodb-base/conf/mongokey && chmod 700 ./mongodb-base/conf/mongokey' % auth['key'], logging.debug)
        tmp =  '--keyFile=%s/conf/mongokey ' % path
        cmd += tmp

    _copy_files(mongos)
    print _remote_run(conf.USER, host, cmd)

def mongos_ps(mongos, configdb):
    [host, port, path] = mongos

    cmd = '''pgrep -l -f  './bin/mongos --configdb %s --logpath ./log/mongod.log --port %d --fork' ''' % (configdb, port)
    print _remote_run(conf.USER, host, cmd)

def mongos_kill(mongos, configdb):
    [host, port, path] = mongos

    cmd = '''pkill -f  './bin/mongos --configdb %s --logpath ./log/mongod.log --port %d --fork' ''' % (configdb, port)
    print _remote_run(conf.USER, host, cmd)

############# sharding op
def _sharding_status(sharding, auth):
    [ip, port, path] = sharding['mongos'][0]
    _run_js(ip, port, 'sh.status()', auth)

def sharding_start(sharding):
    auth = sharding['auth']
    configdb = ['%s:%d' % (i[0], i[1]) for i in sharding['configserver']]
    configdb = ','.join(configdb)

    logging.notice('............. start shard ')
    for shard in sharding['shard']:
        if shard['type'] == 'replset':
            replset_start(shard, auth)
        elif shard['type'] == 'mongod':
            mongod_start(shard['server'], auth=auth)

    logging.notice('............. start configserver ')

    for configserver in sharding['configserver']:
        configserver_start(configserver, auth)

    logging.notice('............. start mongos ')
    for mongos in sharding['mongos']:
        mongos_start(mongos, configdb, auth)

    @common.retry(Exception, tries=2)
    def add_shard(shard):
        if shard['type'] == 'replset':
            members = ['%s:%d'%(host,port) for (id, (host, port, path)) in enumerate(shard['mongod'])]
            members = ','.join(members)
            js =''' 
            //use admin;
            sh.addShard( "%s/%s" );
            ''' % (shard['replset_name'], members)

        elif shard['type'] == 'mongod':
            host,port,path = shard['server']
            members = '%s:%d'%(host,port)
            js =''' 
            //use admin;
            sh.addShard( "%s" );
            ''' % (members)

        [ip, port, path] = sharding['mongos'][0]
        try: 
            _run_js(ip, port, js, auth)
        except Exception as e: 
            if str(e).find('E11000 duplicate key error index: config.shards.$_id_') >= 0:
                logging.warning('shard already added !!!')
                return 
            if str(e).find('host already used') >= 0:
                logging.warning('shard already added !!!')
                return 
            logging.warning('add shard return error with: \n' + str(e))


    for shard in sharding['shard']:
        add_shard(shard)
    _sharding_status(sharding, auth)

    print "please run:"
    print "sh.enableSharding('report')"
    print "sh.shardCollection('report.jomo_report_2013053116', {uuid:1})"

def sharding_ps(sharding):
    configdb = ['%s:%d' % (i[0], i[1]) for i in sharding['configserver']]
    configdb = ','.join(configdb)

    for shard in sharding['shard']:
        if shard['type'] == 'replset':
            replset_ps(shard)
        elif shard['type'] == 'mongod':
            mongod_ps(shard['server'])

    for mongos in sharding['mongos']:
        mongos_ps(mongos, configdb)

    for configserver in sharding['configserver']:
        configserver_ps(configserver)
    _sharding_status(sharding, None)


def sharding_log(sharding):
    for shard in sharding['shard']:
        if shard['type'] == 'replset':
            replset_log(shard)
        elif shard['type'] == 'mongod':
            mongod_log(shard['server'])
        
    for mongos in sharding['mongos']:
        mongod_log(mongos)
    for configserver in sharding['configserver']:
        mongod_log(configserver)

def sharding_stop(sharding):
    for shard in sharding['shard']:
        if shard['type'] == 'replset':
            replset_stop(shard)
        elif shard['type'] == 'mongod':
            mongod_stop(shard['server'])

    configdb = ['%s:%d' % (i[0], i[1]) for i in sharding['configserver']]
    configdb = ','.join(configdb)

    for mongos in sharding['mongos']:   # use kill for mongos
        mongos_kill(mongos, configdb)

    for configserver in sharding['configserver']:
        configserver_kill(configserver)

def sharding_kill(sharding):
    for shard in sharding['shard']:
        if shard['type'] == 'replset':
            replset_kill(shard)
        elif shard['type'] == 'mongod':
            mongod_kill(shard['server'])

    configdb = ['%s:%d' % (i[0], i[1]) for i in sharding['configserver']]
    configdb = ','.join(configdb)

    for mongos in sharding['mongos']:
        mongos_kill(mongos, configdb)

    for configserver in sharding['configserver']:
        configserver_kill(configserver)

mongos_clean = mongod_clean
configserver_clean = mongod_clean

def sharding_clean(sharding):
    for shard in sharding['shard']:
        if shard['type'] == 'replset':
            replset_clean(shard)
        elif shard['type'] == 'mongod':
            mongod_clean(shard['server'])

    for mongos in sharding['mongos']:
        mongos_clean(mongos)
    for configserver in sharding['configserver']:
        configserver_clean(configserver)

def discover_op():
    sets =  globals().keys()
    sets = [s.replace('replset_', '') for s in sets if s.startswith('replset_')]
    return sets

def discover_cluster():
    sets = [s for s in dir(conf) if s.startswith('cluster')]
    return sets

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('op', choices=discover_op(), 
        help='start/stop/clean mongodb/replset/sharding-cluster')

    parser.add_argument('target', choices=discover_cluster(), help='replset target ')
    args = common.parse_args2(LOGPATH, parser)

    _init()

    cluster = eval('conf.%s' % args.target)
    func = eval('%s_%s' % (cluster['type'], args.op) )
    conf.USER = cluster['user']
    func(cluster)
    #eval('%s_%s(conf.%s)' % (, args.target))

if __name__ == "__main__":
    parse_args()

