import json
import os
import re
import winreg
from pathlib import Path

import requests
import base64
import subprocess
import psutil
import win32api
import win32gui

from PyQt5.QtCore import QRectF

from app.common.config import cfg, VERSION, GITHUB_URL
from app.common.update import release_update, github_proxy
from app.common.logger import logger


TAG = "Util"


class Github:
    def __init__(self, user=None, repositories="Seraphine"):
        self.githubApi = "https://api.github.com"
        self.user = user or GITHUB_URL.rstrip('/').split('/')[-2]
        self.repositories = repositories
        self.sess = requests.session()

    def getReleasesInfo(self):
        url = f"{self.githubApi}/repos/{self.user}/{self.repositories}/releases/latest"

        address = github_proxy(cfg.get(cfg.enableGithubProxy), cfg.get(cfg.githubProxyAddr))
        response = self.sess.get(url, proxies={'https': address} if address else None,
                                 timeout=(5, 15), headers={'Accept': 'application/vnd.github+json',
                                                          'User-Agent': 'Seraphine/' + VERSION})
        if response.status_code == 404:
            return {}
        response.raise_for_status()
        return response.json()

    def checkUpdate(self):
        """
        检查版本更新
        @return: 有更新 -> info, 无更新 -> None
        """
        info = self.getReleasesInfo()

        return release_update(info, VERSION, f'{self.user}/{self.repositories}')

    def getNotice(self):
        url = f'{self.githubApi}/repos/{self.user}/{self.repositories}/contents/document/notice.md'

        if cfg.get(cfg.enableGithubProxy):
            proxy = {'https': cfg.get(cfg.githubProxyAddr)}
        else:
            proxy = None

        res = self.sess.get(url, proxies=proxy).json()

        content = str(base64.b64decode(res['content']), encoding='utf-8')

        return {
            'sha': res['sha'],
            'content': content,
        }


github = Github()


def getLoLPathByRegistry() -> str:
    """
    从注册表获取LOL的安装路径

    ** 只能获取到国服的路径, 外服不支持 **

    无法获取时返回空串
    """
    mainKey = winreg.HKEY_CURRENT_USER
    subKey = "SOFTWARE\Tencent\LOL"
    valueName = "InstallPath"

    try:
        with winreg.OpenKey(mainKey, subKey) as k:
            installPath, _ = winreg.QueryValueEx(k, valueName)
            path = str(Path(f"{installPath}\TCLS").absolute()
                       ).replace("\\", "/")
            return f"{path[:1].upper()}{path[1:]}"
    except FileNotFoundError:
        logger.warning("reg path or val does not exist.", TAG)
    except WindowsError as e:
        logger.warning(f"occurred while reading the registry: {e}", TAG)
    except Exception as e:
        logger.exception("unknown error reading registry", e, TAG)

    return ""


def getLolProcesses():
    """Return (client PIDs, game running), or None if this poll failed."""
    def match_processes(rows):
        clients = []
        game_running = False
        for pid, name in rows:
            name = (name or '').casefold().removesuffix('.exe')
            if name == 'leagueclientux':
                clients.append(pid)
            elif name == 'league of legends':
                game_running = True
        return clients, game_running

    try:
        return match_processes((p.pid, p.info['name'])
                               for p in psutil.process_iter(['name'], ad_value=None))
    except (psutil.Error, OSError):
        # Some Windows installations restrict native process enumeration.
        tasklist = Path(os.environ['SystemRoot']) / 'System32/tasklist.exe'
        try:
            output = subprocess.check_output(
                [str(tasklist), '/FO', 'CSV', '/NH'], stderr=subprocess.DEVNULL,
                timeout=3, creationflags=subprocess.CREATE_NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            return None
        rows = re.findall(rb'^"([^"\r\n]+)","([0-9]+)",', output, re.MULTILINE)
        if rows:
            return match_processes((int(pid), name.decode('ascii', errors='replace'))
                                   for name, pid in rows)
        return None


def getPortTokenServerByPid(pid):
    """Read only the selected client. It can exit between polling and connecting."""
    port, token, server = None, None, None

    process = psutil.Process(pid)
    cmdline = process.cmdline()

    for cmd in cmdline:

        p = cmd.find("--app-port=")
        if p != -1:
            port = cmd[11:]

        p = cmd.find("--remoting-auth-token=")
        if p != -1:
            token = cmd[22:]

        p = cmd.find("--rso_platform_id=")
        if p != -1:
            server = cmd[18:]

        if port and token and server:
            break

    return port, token, server


def getFileProperties(fname):
    """
    读取给定文件的所有属性, 返回一个字典.

    returns : {'FixedFileInfo': {'Signature': -17890115, 'StrucVersion': 65536, 'FileVersionMS': 917513, 'FileVersionLS':
    38012988, 'ProductVersionMS': 917513, 'ProductVersionLS': 38012988, 'FileFlagsMask': 23, 'FileFlags': 0,
    'FileOS': 4, 'FileType': 1, 'FileSubtype': 0, 'FileDate': None}, 'StringFileInfo': {'Comments': None,
    'InternalName': 'League of Legends (TM) Client', 'ProductName': 'League of Legends (TM) Client', 'CompanyName':
    'Riot Games, Inc.', 'LegalCopyright': 'Copyright (C) 2009', 'ProductVersion': '14.9.580.2108', 'FileDescription':
    'League of Legends (TM) Client', 'LegalTrademarks': None, 'PrivateBuild': None, 'FileVersion': '14.9.580.2108',
    'OriginalFilename': 'League of Legends.exe', 'SpecialBuild': None}, 'FileVersion': '14.9.580.2108'}

    """

    propNames = ('Comments', 'InternalName', 'ProductName',
                 'CompanyName', 'LegalCopyright', 'ProductVersion',
                 'FileDescription', 'LegalTrademarks', 'PrivateBuild',
                 'FileVersion', 'OriginalFilename', 'SpecialBuild')

    props = {'FixedFileInfo': None,
             'StringFileInfo': None, 'FileVersion': None}

    try:
        fixedInfo = win32api.GetFileVersionInfo(fname, '\\')
        props['FixedFileInfo'] = fixedInfo
        props['FileVersion'] = "%d.%d.%d.%d" % (fixedInfo['FileVersionMS'] / 65536,
                                                fixedInfo['FileVersionMS'] % 65536, fixedInfo['FileVersionLS'] / 65536,
                                                fixedInfo['FileVersionLS'] % 65536)

        # \VarFileInfo\Translation returns list of available (language, codepage)
        # pairs that can be used to retreive string info. We are using only the first pair.
        lang, codepage = win32api.GetFileVersionInfo(
            fname, '\\VarFileInfo\\Translation')[0]

        # any other must be of the form \StringfileInfo\%04X%04X\parm_name, middle
        # two are language/codepage pair returned from above

        strInfo = {}
        for propName in propNames:
            strInfoPath = u'\\StringFileInfo\\%04X%04X\\%s' % (
                lang, codepage, propName)
            strInfo[propName] = win32api.GetFileVersionInfo(fname, strInfoPath)

        props['StringFileInfo'] = strInfo
    except:
        return {}
    else:
        return props


def getLolClientVersion():
    gamePath = cfg.get(cfg.lolFolder)[0]

    assert gamePath  # 必须有, 否则就是调用逻辑有问题 -- By Hpero4

    # 特判一下国服 -- By Hpero4
    gamePath = gamePath.replace("/TCLS", "")

    lolExe = f"{gamePath}/Game/League of Legends.exe"
    # 判断一下, 客户端特殊? 为啥会没有LOL的主程序 -- By Hpero4
    if not os.path.exists(lolExe):
        raise FileNotFoundError(lolExe)

    fileInfo = getFileProperties(lolExe).get("StringFileInfo", {})
    lolVer = fileInfo.get("ProductVersion") or fileInfo.get("FileVersion")

    assert lolVer

    # 缩短至大版本号
    return re.search(r"\d+\.\d+", lolVer).group(0)


def getLolClientWindowPos() -> QRectF:
    # 获取客户端窗口句柄
    hwnd = win32gui.FindWindow("RCLIENT", "League of Legends")

    # 如果没客户端，就直接 return 一个 None
    if not hwnd:
        return None

    # 获取客户端窗口位置
    # struct RECT {
    #     LONG left;
    #     LONG top;
    #     LONG right;
    #     LONG bottom;
    # }
    rect = win32gui.GetWindowRect(hwnd)

    # 窗口最小化的时候，比例不是 16:9，直接 return 一个 None
    if (rect[3] - rect[1]) / (rect[2] - rect[0]) != 0.5625:
        return None

    return QRectF(rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1])
