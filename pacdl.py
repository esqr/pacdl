#!/usr/bin/env python3
# -*- coding: utf8 -*-

import configparser
import os
import argparse
import requests
import email.utils as eut
import datetime
import tarfile
import sys
import shutil
from tqdm import tqdm
import math

parser = argparse.ArgumentParser(description='pacdl')
parser.add_argument('-y', '--refresh', dest='refresh', action='store_true', help='Sync databases')
parser.add_argument('-u', '--download', dest='download', action='store_true', help='Download packages')
parser.add_argument('-c', '--clean', dest='clean', action='store_true', help='Clean cache')
args = parser.parse_args()


def log(*args, tty_only=False, **kwargs):
    if not tty_only or sys.stdout.isatty():
        print(*args, **kwargs)


class Profile:
    def __init__(self, name, config):
        self.name = name
        self.config = config


class MultiDict(dict):
    def __setitem__(self, key, value, **kwargs):
        if isinstance(value, list) and key in self:
            self[key].extend(value)
        else:
            super(MultiDict, self).__setitem__(key, value)


def convert_size(size_bytes):
    if size_bytes == 0:
        return "0B"
    size_name = ("", "Ki", "Mi", "Gi", "Ti")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return "%s%sB" % (s, size_name[i])


def download_file(url, target_dir, filename=None):
    local_filename = url.split('/')[-1] if filename is None else filename
    r = requests.get(url, stream=True)
    path = os.path.join(target_dir, local_filename)
    result = {'success': False, 'status_code': 0, 'message': ''}

    # Check if file changed
    lm = r.headers.get('Last-Modified')

    if lm is not None:
        last_modified = datetime.datetime(*eut.parsedate(lm)[:6])
    else:
        last_modified = datetime.datetime.now()

    if os.path.isfile(path) and os.path.getmtime(path) == last_modified.timestamp():
        r.close()
        result['success'] = True
        return result

    # Prepare download
    total_size = int(r.headers.get('content-length', 0))
    dloaded = 0

    # Download file
    with open(path + '.part', 'wb') as f:
        pbar = tqdm(total=total_size, unit='B', unit_scale=True)
        for chunk in r.iter_content(32*1024):
            if chunk:
                f.write(chunk)
                dloaded += len(chunk)
                pbar.update(len(chunk))

    # Check if download is successful
    if dloaded != total_size or r.status_code != 200:
        result['status_code'] = r.status_code
        result['message'] = '{}/{}, {}'.format(dloaded, total_size, r.status_code)
        return result

    # Finish download and return
    os.replace(path + '.part', path)
    os.utime(path, (int(datetime.datetime.now().timestamp()), int(last_modified.timestamp())))
    result['success'] = True
    result['status_code'] = r.status_code
    return result


def sync_db():
    log(':: syncing databases')

    db_archs = set()

    for p in profiles:
        for s in p.config.sections():
            db_archs.add(p.config[s]['arch'])

    for repo in repos.sections():
        mirrors = repos[repo]['server'].split('\n')
        for arch in db_archs:
            path = os.path.join(config['paths']['mirror'], repo, arch)
            local_path = os.path.join(config['paths']['local'], repo, arch)

            # Make dirs for repo and arch
            os.makedirs(path, exist_ok=True)
            os.makedirs(local_path, exist_ok=True)

            log('::: downloading db {}/{}'.format(repo, arch))

            downloaded = False

            for mirror in mirrors:
                url = mirror.replace('$repo', repo).replace('$arch', arch) + '/' + repo + '.db'
                log('downloading {}'.format(url))
                status = download_file(url, path)

                if status['status_code'] == 0:
                    log('not modified (probably)')
                    downloaded = True
                    break
                elif status['status_code'] == 200 and status['success']:
                    tar = tarfile.open(os.path.join(config['paths']['mirror'], repo, arch, repo + '.db'))

                    for d in os.listdir(local_path):
                        shutil.rmtree(os.path.join(local_path, d))

                    tar.extractall(local_path)
                    tar.close()
                    downloaded = True
                    break
                else:
                    log(status['message'])

            if not downloaded:
                log('err: couldn\'t sync repo {}/{}'.format(repo, arch))
            log()

    log(':: syncing databases: done')


def sync_packages():
    log(':: syncing packages')
    packages = {}
    packages_count = 0

    for repo in repos.sections():
        packages[repo] = {}

    # Get packages

    for p in profiles:
        for s in p.config.sections():
            arch = p.config[s]['arch']
            file = p.config[s]['packages']

            with open(os.path.join(profiles_path, p.name, file)) as f:
                for line in f:
                    r, pkg = line.strip().split(' ')

                    if r in packages:
                        if arch not in packages[r]:
                            packages[r][arch] = {}
                        packages[r][arch][pkg] = None
                    else:
                        log('warn: repo {} not found ({}/{})'.format(r, p.name, s))

    bad_repos = set()

    to_download = 0
    for repo in packages:
        for arch in packages[repo]:
            log('reading {}/{}'.format(repo, arch))

            local_path = os.path.join(config['paths']['local'], repo, arch)

            if not os.path.exists(local_path):
                log('err: local repo {}/{} not found'.format(repo, arch))
                bad_repos.add((repo, arch))
                continue

            db = dict([(lambda s: (s[0], '-'.join(s[1:])))(f.rsplit('-', 2)) for f in os.listdir(local_path)])

            to_remove = set()

            local_files = set(os.listdir(os.path.join(config['paths']['mirror'], repo, arch)))

            for pkg in packages[repo][arch]:
                version = db.get(pkg)
                if version is not None:
                    with open(os.path.join(local_path, pkg + '-' + version, 'desc'), encoding='utf-8') as f:
                        pkgdata = f.readlines()

                    filename = pkgdata[1].strip()

                    if filename in local_files:
                        to_remove.add(pkg)
                    else:
                        size = 0
                        for i in range(len(pkgdata)):
                            if pkgdata[i] == '%CSIZE%\n':
                                size = int(pkgdata[i + 1])
                                break

                        packages[repo][arch][pkg] = {'filename': filename, 'size': size}
                        to_download += size
                else:
                    to_remove.add(pkg)
                    log('warn: package {} not found in repo {}/{}'.format(pkg, repo, arch))

            for k in to_remove:
                del packages[repo][arch][k]

            packages_count += len(packages[repo][arch])

    # Download packages
    log()
    log('::: downloading {} packages ({})'.format(packages_count, convert_size(to_download)))

    for repo in packages:
        mirrors_raw = repos[repo]['server'].split('\n')

        for arch in packages[repo]:
            if (repo, arch) in bad_repos:
                continue

            mirrors = [m.replace('$repo', repo).replace('$arch', arch) for m in mirrors_raw]

            for pkg in packages[repo][arch]:
                log('::: downloading package {} ({}/{})'.format(pkg, repo, arch))

                path = os.path.join(config['paths']['mirror'], repo, arch)
                filename = packages[repo][arch][pkg]['filename']

                downloaded = False

                for mirror in mirrors:
                    url = mirror + '/' + filename
                    log('downloading {}'.format(url))
                    status = download_file(url, path)
                    if status['status_code'] == 200:
                        downloaded = True
                        break
                    else:
                        log(status['message'])
                if not downloaded:
                    log('warn: couldn\'t download {} ({}/{})'.format(pkg, repo, arch))
                log()
    log(':: syncing packages: done')


def clear_cache():
    log(':: cleaning cache')

    db_archs = set()

    packages = {}

    for repo in repos.sections():
        packages[repo] = {}

    for p in profiles:
        for s in p.config.sections():
            db_archs.add(p.config[s]['arch'])

            arch = p.config[s]['arch']
            file = p.config[s]['packages']

            with open(os.path.join(profiles_path, p.name, file)) as f:
                for line in f:
                    r, pkg = line.strip().split(' ')
                    if r in packages:
                        if arch not in packages[r]:
                            packages[r][arch] = {}
                        packages[r][arch][pkg] = None

    repos_mirror = os.listdir(config['paths']['mirror'])
    repos_local = os.listdir(config['paths']['local'])

    log('::: cleaning local')
    removed = []
    for repo in repos_local:
        repo_path = os.path.join(config['paths']['local'], repo)

        if repo not in repos.sections():
            shutil.rmtree(repo_path)
            removed.append(repo)
        else:
            archs = os.listdir(repo_path)
            for arch in archs:
                if arch not in db_archs:
                    removed.append(repo + '/' + arch)
                    shutil.rmtree(os.path.join(repo_path, arch))

    log('deleted repos: {}'.format(', '.join(removed) if len(removed) > 0 else 'nothing'))

    log('::: cleaning mirror')
    removed = []
    removed_pkg = 0
    for repo in repos_mirror:
        repo_path = os.path.join(config['paths']['mirror'], repo)

        if repo not in repos.sections():
            shutil.rmtree(repo_path)
            removed.append(repo)
        else:
            archs = os.listdir(repo_path)
            for arch in archs:
                arch_path = os.path.join(repo_path, arch)
                if arch not in db_archs:
                    shutil.rmtree(arch_path)
                    removed.append(repo + '/' + arch)
                else:
                    packages_cache = set(os.listdir(arch_path))
                    packages_cache.remove(repo + '.db')
                    local_path = os.path.join(config['paths']['local'], repo, arch)
                    db = dict([(lambda s: (s[0], '-'.join(s[1:])))(f.rsplit('-', 2)) for f in os.listdir(local_path)])

                    if arch in packages[repo]:
                        for pkg in packages[repo][arch]:
                            version = db.get(pkg)
                            if version is not None:
                                with open(os.path.join(local_path, pkg + '-' + version, 'desc'), encoding='utf-8') as f:
                                    filename = f.readlines()[1].strip()
                                packages_cache.discard(filename)

                    for f in packages_cache:
                        shutil.rmtree(os.path.join(arch_path, f), ignore_errors=True)
                        removed_pkg += 1

    log('deleted: {}\ncleaned {} files'.format(', '.join(removed) if len(removed) > 0 else 'nothing', removed_pkg))
    log(':: cleaning cache: done')


# Read config

config = configparser.ConfigParser()
config.read(os.path.join(os.path.dirname(__file__), 'config'))

lock_file = config['paths']['lock_file']

if os.path.isfile(lock_file):
    log('Databases locked; exiting')
    exit(1)

lock = open(lock_file, 'wb')

# Read repos

repos = configparser.ConfigParser(strict=False, dict_type=MultiDict)
repos.read(os.path.join(os.path.dirname(__file__), 'repos'))

for r in repos.sections():
    if 'include' in repos[r]:
        inc_file = repos[r]['include']
        inc_str = '[servers]\n'
        with open(inc_file, 'r') as f:
            inc_str += f.read()
        inc_cfg = configparser.ConfigParser(strict=False, dict_type=MultiDict)
        inc_cfg.read_string(inc_str)
        repos[r].update(inc_cfg['servers'])


# Create dirs
os.makedirs(config['paths']['mirror'], exist_ok=True)
os.makedirs(config['paths']['local'], exist_ok=True)
os.makedirs(config['paths']['profiles'], exist_ok=True)

# Read profiles

profiles = set()

profiles_path = config['paths']['profiles']
profile_dirs = [d for d in os.listdir(profiles_path) if os.path.isdir(os.path.join(profiles_path, d))]

for pd in profile_dirs:
    pc = configparser.ConfigParser()
    pc.read(os.path.join(profiles_path, pd, 'config'))
    profile = Profile(pd, pc)
    profiles.add(profile)

if not args.refresh and not args.download and not args.clean:
    log('nothing to do')

if args.refresh:
    sync_db()

if args.clean:
    if args.refresh:
        log()
    clear_cache()

if args.download:
    if args.refresh or args.clean:
        log()
    sync_packages()

lock.close()
os.remove(lock_file)
