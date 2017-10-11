# pacdl
script to download/mirrorize Arch Linux packages on other machines/distros

## Requirements
* python3
* [tqdm](https://github.com/tqdm/tqdm)
* [requests](https://github.com/requests/requests)

## Configuration
Examples of config files are included, you just have to customize them to fit your needs.

### `config`
Main config file. Currently there are only paths to dirs and lock file.

### `repos`
List of repositories. Same format as in `/etc/pacman.conf`

### Profiles
Profiles are stored in `profiles` path provided in `config` file. Each profile is a directory which contains `config` file and package list files.

#### `config`
Contains subprofiles eg.:

    [foo]
    arch = x86_64
    packages = packages_foo

    [bar]
    arch = i686
    packages = packages_bar

#### package list file example

    extra firefox
    core linux
    repo-ck linux-ck
    extra vim
    extra vim-runtime
    extra zsh
    
To get list from installed packages you can use this one-liner:

    LIST=$(pacman -Sl); for PKG in $(pacman -Qq); do echo "$LIST" | grep " $PKG "; done | cut -d' ' -f1,2
       
## Usage
`python3 pacdl.py [-h] [-y] [-u] [-c]`

### Arguments
    -h, --help      show help message and exit
    -y, --refresh   Sync databases
    -u, --download  Download packages
    -c, --clean     Clean cache
