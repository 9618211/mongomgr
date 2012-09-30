#!/usr/bin/env python
#coding: utf-8
#file   : deploy.py
#author : ning
#date   : 2012-08-24 06:26:25


import urllib, urllib2, httplib, os, re, sys, time, logging, hmac, base64, commands, glob
import json
from common import *
import argparse
import config
import socket

def _system(cmd):
    print_green('[info] ' + cmd)
    return system(cmd)

def _init():
    _system('rm -rf ./mongodb-base')
    _system('cp -rf %s ./mongodb-base' % config.MONGO_DB_PATH)
    _system('mkdir -p ./mongodb-base/conf')
    _system('mkdir -p ./mongodb-base/log')
    _system('mkdir -p ./mongodb-base/db')
    _system('cp mongod.conf ./mongodb-base/conf')

def _toip(replset):
    for i in replset:
        i[0] = socket.gethostbyname(i[0])
    return replset
    
def _deploy_single(host, port, path):

    cmd = 'ssh -n -f %s@%s "mkdir -p %s "' % (config.USER, host, path)
    _system(cmd)

    cmd = 'rsync -avP ./mongodb-base/ %s@%s:%s 1>/dev/null 2>/dev/null' % (config.USER, host, path)
    _system(cmd)

    cmd = 'ssh -n -f %s@%s "cd %s ; ./bin/mongod -f ./conf/mongod.conf --port %d --fork "' % (config.USER, host, path, port)
    print _system(cmd)

################################### op
def stop(replset):
    for host, port, path in  replset:
        cmd = 'ssh -n -f %s@%s "cd %s ; ./bin/mongod -f ./conf/mongod.conf --port %d --shutdown"' % (config.USER, host, path, port)
        print _system(cmd)

def clean(replset):
    for host, port, path in  replset:
        cmd = 'ssh %s@%s "rm -rf %s "' % (config.USER, host, path)
        print _system(cmd)

def start(replset):
    for host, port, path in  replset:
        _deploy_single(host, port, path)
    time.sleep(5)
    members = [{'_id': id, 'host': '%s:%d'%(host,port) } for (id, (host, port, path)) in enumerate(replset)]
    replset_config = {
        '_id': 'cluster0',
        'members': members
    }
    js = '''
config = %s;
rs.initiate(config);
''' % json.dumps(replset_config)

    f = file('tmp.js', 'w')
    f.write(js)
    f.close()
    print 'tmp.js: ', js
    
    primary = replset[0]
    ip = socket.gethostbyname(primary[0])
    port = primary[1]
    cmd = './mongodb-base/bin/mongo %s:%d %s' % (ip, port, 'tmp.js')
    print _system(cmd)
    print_green( 'see http://%s:%d/_replSet' % (primary[0], 1000+primary[1]) )


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('op', choices=['start', 'stop', 'clean'], 
        help='start/stop/clean mongodb cluster')
    sets = [s for s in dir(config) if s.startswith('cluster')]
    parser.add_argument('target', choices=sets , help='replset target ')
    args = parser.parse_args()


    _init()
    #print args
    eval('%s(_toip(config.%s))' % (args.op, args.target))

if __name__ == "__main__":
    parse_args()
