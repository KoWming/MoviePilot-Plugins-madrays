"""
NexusPHP站点邀请系统管理插件
"""
import os
import json
import time
import requests
from urllib.parse import urljoin
from typing import Any, List, Dict, Tuple, Optional
from datetime import datetime
from bs4 import BeautifulSoup
import re

from app.core.config import settings
from app.plugins import _PluginBase
from app.log import logger
from app.schemas import Response
from app.schemas.types import NotificationType
from app.core.event import eventmanager
from app.schemas.types import EventType
from app.db.site_oper import SiteOper
from app.helper.sites import SitesHelper
from apscheduler.triggers.cron import CronTrigger


class nexusinvitee(_PluginBase):
    # 插件名称
    plugin_name = "后宫管理系统"
    # 插件描述
    plugin_desc = "管理NexusPHP站点的邀请系统，包括邀请名额、已邀请用户状态等"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/madrays/MoviePilot-Plugins/main/icons/nexusinvitee.png"
    # 插件版本
    plugin_version = "1.0.0"
    # 插件作者
    plugin_author = "madrays"
    # 作者主页
    author_url = "https://github.com/madrays"
    # 插件配置项ID前缀
    plugin_config_prefix = "nexusinvitee_"
    # 加载顺序
    plugin_order = 21
    # 可使用的用户级别
    auth_level = 2

    # 私有属性
    _config = {}  # 改为统一的配置字典
    _enabled = False
    _notify = False
    _cron = "0 */4 * * *"  # 默认每4小时检查一次
    _onlyonce = False
    _nexus_sites = []  # 改为列表支持多选
    
    # 站点助手
    sites: SitesHelper = None
    siteoper: SiteOper = None
    
    # 配置文件路径
    config_file = None

    def init_plugin(self, config=None):
        """
        插件初始化
        """
        self.sites = SitesHelper()
        self.siteoper = SiteOper()
        
        # 获取数据目录
        data_path = self.get_data_path()
        
        # 确保目录存在
        if not os.path.exists(data_path):
            try:
                os.makedirs(data_path)
            except Exception as e:
                logger.error(f"创建数据目录失败: {str(e)}")
        
        # 设置配置文件路径
        self.config_file = os.path.join(data_path, "config.json")
        
        # 初始化时从文件加载配置到内存
        self._sync_from_file()

        # 刷新插件配置
        if config:
            self._config = config
            # 保存配置
            self._site_ids = config.get("site_ids", [])
            self._cron = config.get("cron")
            self._onlyonce = config.get("onlyonce", False)
            self._save_config()

            # 立即刷新数据开关
            if self._onlyonce:
                # 关闭开关
                self._config['onlyonce'] = False
                self._onlyonce = False
                self._save_config()
                # 立即刷新
                logger.info(f"手动触发刷新站点数据...")
                self.post_message(
                    mtype=NotificationType.SiteMessage,
                    title="后宫管理系统",
                    text="正在刷新所有站点后宫数据..."
                )
                self.refresh_all_sites()
        
        # 统计实际选中的站点数量
        selected_sites = len([site for site in self.sites.get_indexers() 
                              if str(site.get("id")) in [str(x) for x in self._site_ids]])
        logger.info(f"后宫管理系统初始化完成，已选择 {selected_sites} 个站点")

    def _sync_from_file(self):
        """
        从配置文件同步到内存
        """
        if not os.path.exists(self.config_file):
            return False

        try:
            # 读取文件内容
            with open(self.config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
            
            # 更新内存中的配置
            self._config = config
            self._enabled = bool(config.get("enabled", False))
            self._notify = bool(config.get("notify", False))
            self._cron = str(config.get("cron", "0 */4 * * *"))
            self._onlyonce = bool(config.get("onlyonce", False))
            self._site_ids = config.get("site_ids", [])
            return True
        except Exception as e:
            logger.error(f"读取配置文件失败: {str(e)}")
            return False

    def _sync_to_file(self):
        """
        将内存配置同步到文件
        """
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self._config, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.error(f"保存配置到文件失败: {str(e)}")
            return False

    def __update_config(self):
        """
        更新配置
        """
        self._config.update({
            "enabled": self._enabled,
            "notify": self._notify,
            "cron": self._cron,
            "onlyonce": self._onlyonce,
            "site_ids": self._site_ids
        })
        self._sync_to_file()

    def get_state(self) -> bool:
        """
        获取插件状态
        """
        return True if self._config else False

    def get_command(self) -> List[Dict[str, Any]]:
        """
        注册插件命令
        """
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        """
        获取插件API
        """
        return [{
            "path": "/config",
            "endpoint": self.get_config,
            "methods": ["GET"],
            "summary": "获取配置",
            "description": "获取后宫管理系统配置数据",
        }, {
            "path": "/update_config",
            "endpoint": self.update_config,
            "methods": ["POST"],
            "summary": "更新配置",
            "description": "更新后宫管理系统配置数据",
        }, {
            "path": "/get_invitees",
            "endpoint": self.get_invitees,
            "methods": ["GET"],
            "summary": "获取被邀请人列表",
            "description": "获取所有站点的被邀请人列表及状态",
        }]

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        配置页面
        """
        # 获取支持的站点列表
        site_options = []
        for site in self.sites.get_indexers():
            site_name = site.get("name", "")
            site_options.append({
                "title": site_name,
                "value": site.get("id")
            })
        
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enabled',
                                            'label': '启用插件'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'notify',
                                            'label': '发送通知'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'onlyonce',
                                            'label': '立即刷新数据'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12
                                },
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'model': 'site_ids',
                                            'label': '选择站点',
                                            'items': site_options,
                                            'multiple': True,
                                            'chips': True,
                                            'clearable': True,
                                            'persistent-hint': True,
                                            'hint': '选择要管理的站点，支持多选'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12
                                },
                                'content': [
                                    {
                                        'component': 'VCronField',
                                        'props': {
                                            'model': 'cron',
                                            'label': '执行周期'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12
                                },
                                'content': [
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'info',
                                            'variant': 'tonal',
                                            'text': '【使用说明】\n本插件适配各站点中，不排除bug，目前尚未适配mt、ptt、ttg等，以及我没有的站点，欢迎大佬们报错时提交错误站点的邀请页和发邀页html结构\n1. 选择要管理的站点（支持多选）\n2. 设置执行周期，建议每4小时检查一次\n3. 可选择开启通知，在状态变更时收到通知\n4. 需要手动刷新数据时，打开"立即刷新数据"并保存\n5. 数据默认缓存6小时，打开详情页不会自动刷新数据'
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enabled": self._enabled,
            "notify": self._notify,
            "cron": self._cron,
            "onlyonce": self._onlyonce,
            "site_ids": self._site_ids
        }

    def _is_nexusphp(self, site_url: str) -> bool:
        """
        判断是否为NexusPHP站点
        """
        # 简单判断，后续可以添加更多特征
        return "php" in site_url.lower()

    def get_page(self) -> List[dict]:
        """
        详情页面
        """
        try:
            # 确保先从配置文件加载最新数据
            self._sync_from_file()

            # 直接从配置文件中读取缓存的数据，而不是重新获取
            cached_data = self._config.get("cached_data", {})
            last_update = "未知"
            if cached_data:
                # 找出最新更新时间
                update_times = [cache.get("last_update", 0)
                                for cache in cached_data.values() if cache]
                if update_times:
                    last_update = time.strftime(
                        "%Y-%m-%d %H:%M:%S", time.localtime(max(update_times)))

            # 准备页面内容
            page_content = []

            # 添加头部信息和提示
            page_content.append({
                "component": "VAlert",
                "props": {
                    "type": "info",
                    "text": f"后宫管理系统 - 共 {len(cached_data)} 个站点，数据最后更新时间: {last_update}\n注: 数据默认缓存6小时，只显示在MP中已配置并选择的站点",
                    "variant": "tonal",
                    "class": "mb-4"
                }
            })

            # 添加说明提示
            page_content.append({
                "component": "VAlert",
                "props": {
                    "type": "warning",
                    "text": "如需刷新数据，请在配置页面打开\"立即刷新数据\"开关并保存",
                    "variant": "tonal",
                    "class": "mb-4"
                }
            })

            # 准备站点卡片
            cards = []
            
            # 计算所有站点统计信息
            total_sites = len(cached_data)
            total_invitees = 0
            total_low_ratio = 0
            total_banned = 0

            for site_name, cache in cached_data.items():
                invite_data = cache.get("data", {})
                invitees = invite_data.get("invitees", [])
                total_invitees += len(invitees)

                # 计算分享率低和被ban的用户
                for invitee in invitees:
                    # 检查是否被ban
                    if invitee.get('enabled', '').lower() == 'no':
                        total_banned += 1

                    # 检查分享率是否低于1
                    ratio_str = invitee.get('ratio', '')
                    if ratio_str != '∞' and ratio_str.lower() != 'inf.' and ratio_str.lower() != 'inf':
                        try:
                            # 标准化字符串，替换逗号为点
                            ratio_str = ratio_str.replace(',', '.')
                            ratio_val = float(ratio_str) if ratio_str else 0
                            if ratio_val < 1:
                                total_low_ratio += 1
                        except (ValueError, TypeError):
                            # 转换错误时记录警告
                            logger.warning(f"分享率转换失败: {ratio_str}")

            # 添加全局统计信息
            page_content.append({
                "component": "VCard",
                "props": {
                    "class": "mb-4",
                    "variant": "outlined"
                },
                "content": [
                    {
                        "component": "VCardTitle",
                        "text": "后宫总览"
                    },
                    {
                        "component": "VCardText",
                        "content": [
                            {
                                "component": "VRow",
                                "content": [
                                    {
                                        "component": "VCol",
                                        "props": {"cols": 3},
                                        "content": [{
                                            "component": "div",
                                            "props": {
                                                "class": "text-center"
                                            },
                                            "content": [
                                                {
                                                    "component": "div",
                                                    "props": {"class": "text-h5"},
                                                    "text": str(total_sites)
                                                },
                                                {
                                                    "component": "div",
                                                    "props": {"class": "text-caption"},
                                                    "text": "站点数量"
                                                }
                                            ]
                                        }]
                                    },
                                    {
                                        "component": "VCol",
                                        "props": {"cols": 3},
                                        "content": [{
                                            "component": "div",
                                            "props": {
                                                "class": "text-center"
                                            },
                                            "content": [
                                                {
                                                    "component": "div",
                                                    "props": {"class": "text-h5"},
                                                    "text": str(total_invitees)
                                                },
                                                {
                                                    "component": "div",
                                                    "props": {"class": "text-caption"},
                                                    "text": "后宫成员"
                                                }
                                            ]
                                        }]
                                    },
                                    {
                                        "component": "VCol",
                                        "props": {"cols": 3},
                                        "content": [{
                                            "component": "div",
                                            "props": {
                                                "class": "text-center"
                                            },
                                            "content": [
                                                {
                                                    "component": "div",
                                                    "props": {"class": "text-h5 warning--text"},
                                                    "text": str(total_low_ratio)
                                                },
                                                {
                                                    "component": "div",
                                                    "props": {"class": "text-caption"},
                                                    "text": "低分享率成员"
                                                }
                                            ]
                                        }]
                                    },
                                    {
                                        "component": "VCol",
                                        "props": {"cols": 3},
                                        "content": [{
                                            "component": "div",
                                            "props": {
                                                "class": "text-center"
                                            },
                                            "content": [
                                                {
                                                    "component": "div",
                                                    "props": {"class": "text-h5 error--text"},
                                                    "text": str(total_banned)
                                                },
                                                {
                                                    "component": "div",
                                                    "props": {"class": "text-caption"},
                                                    "text": "已禁用成员"
                                                }
                                            ]
                                        }]
                                    }
                                ]
                            }
                        ]
                    }
                ]
            })

            for site_name, cache in cached_data.items():
                invite_data = cache.get("data", {})

                # 获取站点信息
                site_info = None
                for site in self.sites.get_indexers():
                    if site.get("name") == site_name:
                        site_info = site
                        break
                
                if site_info:
                    # 计算此站点的统计信息
                    invitees = invite_data.get("invitees", [])
                    banned_count = sum(1 for i in invitees if i.get(
                        'enabled', '').lower() == 'no')
                    low_ratio_count = 0

                    for invitee in invitees:
                        ratio_str = invitee.get('ratio', '')
                        if ratio_str != '∞' and ratio_str.lower() != 'inf.' and ratio_str.lower() != 'inf':
                            try:
                                # 标准化字符串，替换逗号为点
                                ratio_str = ratio_str.replace(',', '.')
                                ratio_val = float(
                                    ratio_str) if ratio_str else 0
                                if ratio_val < 1:
                                    low_ratio_count += 1
                            except (ValueError, TypeError):
                                # 转换错误时记录警告
                                logger.warning(f"分享率转换失败: {ratio_str}")

                    # 合并站点信息和数据到一张卡片
                    site_card = {
                        "component": "VCard",
                        "props": {
                            "class": "mb-4"
                        },
                        "content": [
                            # 站点信息头部
                            {
                                "component": "VCardItem",
                                "props": {
                                    "class": "py-2"
                                },
                                "content": [
                                    {
                                        "component": "VCardTitle",
                                        "content": [
                                            {
                                                "component": "div",
                                                "props": {
                                                    "class": "d-flex align-center"
                                                },
                                                "content": [
                                                    {
                                                        "component": "VAvatar",
                                                        "props": {
                                                            "size": "24",
                                                            "class": "mr-2"
                                                        },
                                                        "content": [{
                                                            "component": "VImg",
                                                            "props": {
                                                                "src": site_info.get("icon", ""),
                                                                "alt": site_name
                                                            }
                                                        }]
                                                    },
                                                    {
                                                        "component": "span",
                                                        "props": {
                                                            "class": "text-h6"
                                                        },
                                                        "text": f"{site_name} - 后宫: {len(invitees)}人 (低分享率: {low_ratio_count}, 已禁用: {banned_count})"
                                                    }
                                                ]
                                            }
                                        ]
                                    }
                                ]
                            },
                            # 邀请状态统计
                            {
                                "component": "VCardText",
                                "props": {
                                    "class": "pt-2 pb-0"
                                },
                                "content": [
                                    {
                                        "component": "VRow",
                                        "props": {
                                            "dense": True
                                        },
                                        "content": [
                                            {
                                                "component": "VCol",
                                                "props": {"cols": 12},
                                                "content": [{
                                                    "component": "VAlert",
                                                    "props": {
                                                        "type": "error" if not invite_data.get("invite_status", {}).get("can_invite") else "success",
                                                        "text": invite_data.get("invite_status", {}).get("reason") or (
                                                            "可以发送邀请" if invite_data.get("invite_status", {}).get("can_invite") 
                                                            else "不能发送邀请"
                                                        ),
                                                        "variant": "tonal",
                                                        "density": "compact"
                                                    }
                                                }]
                                            },
                                            {
                                                "component": "VCol",
                                                "props": {"cols": 3},
                                                "content": [{
                                                    "component": "div",
                                                    "props": {
                                                        "class": "text-center"
                                                    },
                                                    "content": [
                                                        {
                                                            "component": "div",
                                                            "props": {"class": "text-h6"},
                                                            "text": str(invite_data.get("invite_status", {}).get("permanent_count", 0))
                                                        },
                                                        {
                                                            "component": "div",
                                                            "props": {"class": "text-caption"},
                                                            "text": "永久邀请"
                                                        }
                                                    ]
                                                }]
                                            },
                                            {
                                                "component": "VCol",
                                                "props": {"cols": 3},
                                                "content": [{
                                                    "component": "div",
                                                    "props": {
                                                        "class": "text-center"
                                                    },
                                                    "content": [
                                                        {
                                                            "component": "div",
                                                            "props": {"class": "text-h6"},
                                                            "text": str(invite_data.get("invite_status", {}).get("temporary_count", 0))
                                                        },
                                                        {
                                                            "component": "div",
                                                            "props": {"class": "text-caption"},
                                                            "text": "临时邀请"
                                                        }
                                                    ]
                                                }]
                                            },
                                            {
                                                "component": "VCol",
                                                "props": {"cols": 3},
                                                "content": [{
                                                    "component": "div",
                                                    "props": {
                                                        "class": "text-center"
                                                    },
                                                    "content": [
                                                        {
                                                            "component": "div",
                                                            "props": {"class": "text-h6"},
                                                            "text": str(len(invite_data.get("invitees", [])))
                                                        },
                                                        {
                                                            "component": "div",
                                                            "props": {"class": "text-caption"},
                                                            "text": "已邀请用户"
                                                        }
                                                    ]
                                                }]
                                            },
                                            {
                                                "component": "VCol",
                                                "props": {"cols": 3},
                                                "content": [{
                                                    "component": "div",
                                                    "props": {
                                                        "class": "text-center"
                                                    },
                                                    "content": [
                                                        {
                                                            "component": "div",
                                                            "props": {"class": "text-h6 " + ("success--text" if invite_data.get("invite_status", {}).get("can_invite") else "error--text")},
                                                            "text": "可邀请" if invite_data.get("invite_status", {}).get("can_invite") else "不可邀请"
                                                        },
                                                        {
                                                            "component": "div",
                                                            "props": {"class": "text-caption"},
                                                            "text": "邀请权限"
                                                        }
                                                    ]
                                                }]
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    }

                    # 只有在有邀请列表时才添加表格
                    if invite_data.get("invitees"):
                        table_rows = []
                        for invitee in invite_data.get("invitees", []):
                            # 判断用户是否被ban或分享率较低
                            is_banned = invitee.get(
                                'enabled', '').lower() == 'no'
                            is_low_ratio = False

                            # 处理分享率
                            ratio_str = invitee.get('ratio', '')
                            if ratio_str != '∞' and ratio_str.lower() != 'inf.' and ratio_str.lower() != 'inf':
                                try:
                                    # 标准化字符串，替换逗号为点
                                    ratio_str = ratio_str.replace(',', '.')
                                    # 尝试转换为浮点数
                                    ratio_val = float(
                                        ratio_str) if ratio_str else 0
                                    is_low_ratio = ratio_val < 1
                                except (ValueError, TypeError):
                                    # 转换失败不做特殊处理
                                    logger.warning(f"分享率转换失败: {ratio_str}")
                                    pass

                            row_class = ""
                            if is_banned:
                                row_class = "error lighten-4"
                            elif is_low_ratio:
                                row_class = "warning lighten-4"

                            # 判断分享率样式
                            ratio_class = ""
                            if ratio_str == '∞' or ratio_str.lower() == 'inf.' or ratio_str.lower() == 'inf':
                                ratio_class = "success--text"
                            else:
                                try:
                                    ratio_val = float(ratio_str.replace(
                                        ',', '.')) if ratio_str else 0
                                    ratio_class = "success--text" if ratio_val >= 1 else "error--text font-weight-bold"
                                except (ValueError, TypeError):
                                    ratio_class = ""

                            # 创建行
                            table_rows.append({
                                "component": "tr",
                                "props": {
                                    "class": row_class
                                },
                                "content": [
                                    {
                                        "component": "td",
                                        "content": [{
                                            "component": "VBtn",
                                            "props": {
                                                "variant": "text",
                                                "href": invitee.get("profile_url", ""),
                                                "target": "_blank",
                                                "density": "compact"
                                            },
                                            "text": invitee.get("username", "")
                                        }]
                                    },
                                    {"component": "td",
                                        "text": invitee.get("email", "")},
                                    {"component": "td", "text": invitee.get(
                                        "uploaded", "")},
                                    {"component": "td", "text": invitee.get(
                                        "downloaded", "")},
                                    {
                                        "component": "td",
                                        "props": {
                                            "class": ratio_class
                                        },
                                        "text": invitee.get("ratio", "")
                                    },
                                    {"component": "td", "text": invitee.get(
                                        "seeding", "")},
                                    {"component": "td", "text": invitee.get(
                                        "seeding_size", "")},
                                    {"component": "td", "text": invitee.get(
                                        "seed_magic", "") or invitee.get("seed_time", "")},
                                    {"component": "td", "text": invitee.get(
                                        "seed_bonus", "")},
                                    {"component": "td", "text": invitee.get(
                                        "last_seed_report", "") or invitee.get("last_seen", "")},
                                    {
                                        "component": "td",
                                        "props": {
                                            "class": ("success--text" if invitee.get('status') == '已确认' else "") +
                                                     (" error--text font-weight-bold" if invitee.get('enabled', '').lower() == 'no' else "")
                                        },
                                        "text": invitee.get("status", "") + (" (已禁用)" if invitee.get('enabled', '').lower() == 'no' else "")
                                    }
                                ]
                            })

                        site_card["content"].append({
                            "component": "VCardText",
                            "props": {
                                "class": "pt-0"
                            },
                            "content": [{
                                "component": "VTable",
                                "props": {
                                    "hover": True,
                                    "density": "compact"
                                },
                                "content": [{
                                    "component": "thead",
                                    "content": [{
                                        "component": "tr",
                                        "content": [
                                            {"component": "th", "text": "用户名"},
                                            {"component": "th", "text": "邮箱"},
                                            {"component": "th", "text": "上传量"},
                                            {"component": "th", "text": "下载量"},
                                            {"component": "th", "text": "分享率"},
                                            {"component": "th", "text": "做种数"},
                                            {"component": "th", "text": "做种体积"},
                                            {"component": "th", "text": "做种时魔"},
                                            {"component": "th", "text": "后宫加成"},
                                            {"component": "th", "text": "最后做种报告"},
                                            {"component": "th", "text": "状态"}
                                        ]
                                    }]
                                }, {
                                    "component": "tbody",
                                    "content": table_rows
                                }]
                            }]
                        })
                    
                    cards.append(site_card)
            
            # 将站点卡片添加到页面
            page_content.extend(cards)

            # 添加说明提示
            if not cards:
                page_content.append({
                    "component": "VAlert",
                    "props": {
                        "type": "warning",
                        "text": "暂无数据，请先在配置中选择要管理的站点，并打开\"立即刷新数据\"开关获取数据",
                        "variant": "tonal",
                        "class": "mt-4"
                    }
                })

            return page_content
            
        except Exception as e:
            logger.error(f"生成详情页面失败: {str(e)}")
            return [{
                "component": "VAlert",
                "props": {
                    "type": "error",
                    "text": f"生成详情页面失败: {str(e)}"
                }
            }]

    def stop_service(self):
        """
        退出插件
        """
        logger.info("后宫管理系统插件停止服务")

    def get_config(self, apikey: str) -> Response:
        """
        获取配置
        """
        if apikey != settings.API_TOKEN:
            return Response(success=False, message="API令牌错误!")
        
        try:
            return Response(success=True, message="获取成功", data=self._config)
        except Exception as e:
            logger.error(f"获取配置失败: {str(e)}")
            return Response(success=False, message=f"获取配置失败: {str(e)}")

    def update_config(self, request: dict) -> Response:
        """
        更新配置
        """
        try:
            # 更新内存中的配置
            self._config.update(request)
            
            # 同步到文件
            if self._sync_to_file():
                return Response(success=True, message="更新成功")
            else:
                return Response(success=False, message="保存配置失败")
        except Exception as e:
            logger.error(f"更新配置失败: {str(e)}")
            return Response(success=False, message=f"更新配置失败: {str(e)}")

    def _get_site_invite_data(self, site_name):
        """
        获取站点邀请页面数据
        """
        try:
            # 获取站点信息
            site_info = None
            for indexer in self.sites.get_indexers():
                if indexer.get("name") == site_name:
                    site_info = indexer
                    break
                    
            if not site_info:
                logger.error(f"站点 {site_name} 信息不存在")
                return {
                    "error": "站点信息不存在",
                    "invite_status": {
                        "can_invite": False,
                        "permanent_count": 0,
                        "temporary_count": 0,
                        "reason": "站点信息不存在"
                    }
                }
                
            site_url = site_info.get("url", "").strip()
            site_cookie = site_info.get("cookie", "").strip()
            ua = site_info.get("ua", "").strip()
            site_id = site_info.get("id", "")

            # 先验证此站点是否在用户选择的站点列表中
            if str(site_id) not in [str(x) for x in self._site_ids]:
                logger.warning(f"站点 {site_name} 不在用户选择的站点列表中，跳过处理")
                return {
                    "error": "站点未被选择",
                    "invite_status": {
                        "can_invite": False,
                        "permanent_count": 0,
                        "temporary_count": 0,
                        "reason": "站点未被选择"
                    }
                }
            
            if not all([site_url, site_cookie, ua]):
                logger.error(f"站点 {site_name} 信息不完整")
                return {
                    "error": "站点信息不完整，请在站点管理中完善配置",
                    "invite_status": {
                        "can_invite": False,
                        "permanent_count": 0,
                        "temporary_count": 0,
                        "reason": "站点信息不完整，请在站点管理中完善配置"
                    }
                }

            # 构建请求Session
            session = requests.Session()
            session.headers.update({
                'User-Agent': ua,
                'Cookie': site_cookie,
                'Referer': site_url,
                'Accept': 'application/json, text/plain, */*',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'Cache-Control': 'no-cache',
                'Pragma': 'no-cache',
                'sec-ch-ua': '"Chromium";v="134", "Not:A-Brand";v="24"',
                'sec-ch-ua-mobile': '?0',
                'sec-ch-ua-platform': '"Windows"',
                'sec-fetch-dest': 'empty',
                'sec-fetch-mode': 'cors',
                'sec-fetch-site': 'same-origin'
            })

            # 先测试cookie是否有效
            test_url = urljoin(site_url, "index.php")
            test_response = session.get(test_url, timeout=(5, 15))
            if test_response.status_code == 403:
                logger.error(f"站点 {site_name} 的Cookie已失效，请更新")
                return {
                    "error": "Cookie已失效，请更新站点Cookie",
                    "invite_status": {
                        "can_invite": False,
                        "permanent_count": 0,
                        "temporary_count": 0,
                        "reason": "Cookie已失效，请更新站点Cookie"
                    }
                }

            # 获取用户ID
            user_id = self._get_user_id(session, site_info)
            if not user_id:
                logger.error(f"无法获取站点 {site_name} 的用户ID")
                return {
                    "error": "无法获取用户ID，请检查站点Cookie是否有效",
                    "invite_status": {
                        "can_invite": False,
                        "permanent_count": 0,
                        "temporary_count": 0,
                        "reason": "无法获取用户ID，请检查站点Cookie是否有效"
                    }
                }

            # 根据站点类型获取数据
            if "m-team" in site_url.lower():
                # 获取API认证信息
                api_key = site_info.get("api_key")
                auth_header = site_info.get("authorization")
                
                if not api_key or not auth_header:
                    logger.error(f"站点 {site_name} API认证信息不完整")
                    return {
                        "error": "API认证信息不完整，请在站点设置中配置API Key和Authorization",
                        "invite_status": {
                            "can_invite": False,
                            "permanent_count": 0,
                            "temporary_count": 0,
                            "reason": "API认证信息不完整，请在站点设置中配置API Key和Authorization"
                        }
                    }
                
                # 更新请求头
                session.headers.update({
                    'Authorization': auth_header,
                    'API-Key': api_key
                })
                
                try:
                    # 获取站点统计数据
                    domain = site_url.split("//")[-1].split("/")[0]
                    api_url = f"https://{domain}/api/v1/site/statistic/{domain}"
                    stats_response = session.get(api_url, timeout=(10, 30))
                    stats_response.raise_for_status()
                    stats_data = stats_response.json()
                    
                    # 获取邀请页面数据
                    invite_url = urljoin(site_url, "invite")
                    invite_response = session.get(invite_url, timeout=(10, 30))
                    invite_response.raise_for_status()
                    
                    return self._parse_mteam_invite_page(invite_response.text, stats_data)
                except requests.exceptions.RequestException as e:
                    logger.error(f"访问M-Team API失败: {str(e)}")
                    return {
                        "error": f"访问站点API失败: {str(e)}",
                        "invite_status": {
                            "can_invite": False,
                            "permanent_count": 0,
                            "temporary_count": 0,
                            "reason": f"访问站点API失败: {str(e)}"
                        }
                    }
            elif "hdchina" in site_url.lower() or "totheglory" in site_url.lower():
                # 特殊站点预留处理位置 - HDChina/TTG等
                logger.warning(f"站点 {site_name} 使用特殊架构，采用通用方法尝试获取数据")
                try:
                    invite_url = urljoin(site_url, f"invite.php?id={user_id}")
                    response = session.get(invite_url, timeout=(10, 30))
                    response.raise_for_status()
                    return self._parse_nexusphp_invite_page(site_name, response.text)
                except requests.exceptions.RequestException as e:
                    logger.error(f"访问特殊站点邀请页面失败: {str(e)}")
                    return {
                        "error": f"访问特殊站点邀请页面失败: {str(e)}",
                        "invite_status": {
                            "can_invite": False,
                            "permanent_count": 0,
                            "temporary_count": 0,
                            "reason": f"访问特殊站点邀请页面失败: {str(e)}"
                        }
                    }
            elif "open.cd" in site_url.lower() or "moecat" in site_url.lower() or "pterclub.com" in site_url.lower():
                # 猫站等特殊处理 - 需要检查坑位
                logger.info(f"处理猫站类型站点: {site_name} ({site_url})")
                try:
                    # 普通邀请页面
                    invite_url = urljoin(site_url, f"invite.php?id={user_id}")
                    response = session.get(invite_url, timeout=(10, 30))
                    response.raise_for_status()
                    result = self._parse_nexusphp_invite_page(
                        site_name, response.text)

                    # 额外检查是否有坑位
                    # 访问发送邀请页面
                    send_invite_url = urljoin(
                        site_url, f"invite.php?id={user_id}&type=new")
                    send_response = session.get(
                        send_invite_url, timeout=(10, 30))
                    send_response.raise_for_status()

                    # 创建BeautifulSoup对象解析页面
                    send_soup = BeautifulSoup(send_response.text, 'html.parser')
                    
                    # 检查是否有takeinvite.php表单
                    has_form = bool(send_soup.select('form[action*="takeinvite.php"]'))
                    
                    # 检查是否有"当前账户上限数已到"的消息
                    no_slot_text = send_soup.find(text=re.compile(r'当前账户上限数已到|帐户上限|没有可用的邀请名额|has reached'))
                    if no_slot_text:
                        result["invite_status"]["can_invite"] = False
                        result["invite_status"]["reason"] = "当前账户上限数已到，没有可用坑位"
                        logger.info(f"站点 {site_name} 坑位已满，无法邀请")
                    elif not has_form:
                        # 如果没有表单，检查是否有其他限制消息
                        sorry_text = send_soup.find(text=re.compile(r'对不起|sorry'))
                        if sorry_text:
                            parent_element = None
                            for parent in sorry_text.parents:
                                if parent.name in ['td', 'div', 'p']:
                                    parent_element = parent
                                    break
                            
                            if parent_element:
                                restriction_text = parent_element.get_text().strip()
                                result["invite_status"]["can_invite"] = False
                                result["invite_status"]["reason"] = restriction_text
                                logger.info(f"站点 {site_name} 有邀请限制: {restriction_text}")
                    elif has_form:
                        # 确认有表单，检查提交按钮
                        submit_buttons = send_soup.select('input[type="submit"]')
                        if submit_buttons:
                            result["invite_status"]["can_invite"] = True
                            if not result["invite_status"]["reason"]:
                                result["invite_status"]["reason"] = "可以发送邀请，有可用坑位"
                            logger.info(f"站点 {site_name} 可以发送邀请，有可用坑位")

                    return result
                except requests.exceptions.RequestException as e:
                    logger.error(f"访问猫站邀请页面失败: {str(e)}")
                    return {
                        "error": f"访问猫站邀请页面失败: {str(e)}",
                        "invite_status": {
                            "can_invite": False,
                            "permanent_count": 0,
                            "temporary_count": 0,
                            "reason": f"访问猫站邀请页面失败: {str(e)}"
                        }
                    }
            else:
                # 标准NexusPHP站点
                try:
                    # 首先获取普通邀请页面的数据
                    invite_url = urljoin(site_url, f"invite.php?id={user_id}")
                    response = session.get(invite_url, timeout=(10, 30))
                    response.raise_for_status()
                    result = self._parse_nexusphp_invite_page(site_name, response.text)
                    
                    # 访问发送邀请页面，这是判断权限的关键
                    send_invite_url = urljoin(site_url, f"invite.php?id={user_id}&type=new")
                    try:
                        send_response = session.get(send_invite_url, timeout=(10, 30))
                        send_response.raise_for_status()
                        
                        # 解析页面
                        send_soup = BeautifulSoup(send_response.text, 'html.parser')
                        
                        # 检查是否有takeinvite.php表单 - 最直接的权限判断
                        invite_form = send_soup.select('form[action*="takeinvite.php"]')
                        if invite_form:
                            # 确认有表单，权限正常
                            result["invite_status"]["can_invite"] = True
                            result["invite_status"]["reason"] = "可以发送邀请"
                            logger.info(f"站点 {site_name} 可以发送邀请，确认有takeinvite表单")
                        else:
                            # 没有表单，检查是否有错误消息
                            sorry_text = send_soup.find(text=re.compile(r'对不起|sorry'))
                            if sorry_text:
                                parent_element = None
                                for parent in sorry_text.parents:
                                    if parent.name in ['td', 'div', 'p', 'h2']:
                                        parent_element = parent
                                        break
                                
                                if parent_element:
                                    # 获取整个限制文本
                                    restriction_text = ""
                                    for parent in parent_element.parents:
                                        if parent.name in ['table']:
                                            restriction_text = parent.get_text().strip()
                                            break
                                    
                                    if not restriction_text:
                                        restriction_text = parent_element.get_text().strip()
                                    
                                    result["invite_status"]["can_invite"] = False
                                    result["invite_status"]["reason"] = restriction_text
                                    logger.info(f"站点 {site_name} 有邀请限制: {restriction_text}")
                            
                    except requests.exceptions.RequestException as e:
                        logger.warning(f"访问站点发送邀请页面失败，使用默认权限判断: {str(e)}")
                    
                    return result
                    
                except requests.exceptions.RequestException as e:
                    logger.error(f"访问邀请页面失败: {str(e)}")
                    return {
                        "error": f"访问邀请页面失败: {str(e)}",
                        "invite_status": {
                            "can_invite": False,
                            "permanent_count": 0,
                            "temporary_count": 0,
                            "reason": f"访问邀请页面失败: {str(e)}"
                        }
                    }

        except Exception as e:
            logger.error(f"获取站点 {site_name} 邀请数据失败: {str(e)}")
            return {
                "error": str(e),
                "invite_status": {
                    "can_invite": False,
                    "permanent_count": 0,
                    "temporary_count": 0,
                    "reason": str(e)
                }
            }

    def _get_user_id(self, session: requests.Session, site_info: dict) -> Optional[str]:
        """
        获取用户ID
        """
        try:
            site_url = site_info.get("url", "").strip()
            
            # 访问个人信息页面
            usercp_url = urljoin(site_url, "usercp.php")
            response = session.get(usercp_url, timeout=(5, 15))
            response.raise_for_status()
            
            # 解析页面获取用户ID
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # 方法1: 从个人信息链接获取
            user_link = soup.select_one('a[href*="userdetails.php"]')
            if user_link and 'href' in user_link.attrs:
                import re
                user_id_match = re.search(r'id=(\d+)', user_link['href'])
                if user_id_match:
                    return user_id_match.group(1)
            
            # 方法2: 从其他链接获取
            invite_link = soup.select_one('a[href*="invite.php"]')
            if invite_link and 'href' in invite_link.attrs:
                user_id_match = re.search(r'id=(\d+)', invite_link['href'])
                if user_id_match:
                    return user_id_match.group(1)
            
            return None
        except Exception as e:
            logger.error(f"获取用户ID失败: {str(e)}")
            return None

    def _convert_size_to_bytes(self, size_str: str) -> float:
        """
        将大小字符串转换为字节数
        """
        if not size_str or size_str.strip() == '':
            logger.warning(f"空的大小字符串")
            return 0

        # 处理特殊情况
        if size_str.lower() == 'inf.' or size_str.lower() == 'inf' or size_str == '∞':
            logger.info(f"识别到无限大值: {size_str}")
            return 1e20  # 使用一个非常大的数值代替无穷大

        try:
            # 标准化字符串，替换逗号为点
            size_str = size_str.replace(',', '.')

            # 分离数字和单位
            # 正则表达式匹配数字部分和单位部分
            matches = re.match(
                r'([\d.]+)\s*([KMGTPEZY]?i?B)', size_str, re.IGNORECASE)

            if not matches:
                # 尝试匹配仅有数字的情况
                try:
                    return float(size_str)
                except ValueError:
                        logger.warning(f"无法解析大小字符串: {size_str}")
                        return 0

            size_num, unit = matches.groups()

            # 尝试转换数字
            try:
                size_value = float(size_num)
            except ValueError:
                logger.warning(f"无法转换大小值为浮点数: {size_num}")
                return 0

            # 单位转换
            unit = unit.upper()

            units = {
                'B': 1,
                'KB': 1024,
                'KIB': 1024,
                'MB': 1024 ** 2,
                'MIB': 1024 ** 2,
                'GB': 1024 ** 3,
                'GIB': 1024 ** 3,
                'TB': 1024 ** 4,
                'TIB': 1024 ** 4,
                'PB': 1024 ** 5,
                'PIB': 1024 ** 5,
                'EB': 1024 ** 6,
                'EIB': 1024 ** 6,
                'ZB': 1024 ** 7,
                'ZIB': 1024 ** 7,
                'YB': 1024 ** 8,
                'YIB': 1024 ** 8
            }

            # 处理简写单位
            if unit in ['K', 'M', 'G', 'T', 'P', 'E', 'Z', 'Y']:
                unit = unit + 'B'

            if unit not in units:
                logger.warning(f"未知的大小单位: {unit}")
                return size_value  # 假设是字节

            return size_value * units[unit]

        except Exception as e:
            logger.warning(f"转换大小字符串到字节时出错 '{size_str}': {str(e)}")
            return 0

    def _calculate_ratio(self, uploaded: str, downloaded: str) -> str:
        """
        计算分享率
        """
        try:
            up_bytes = self._convert_size_to_bytes(uploaded)
            down_bytes = self._convert_size_to_bytes(downloaded)
            
            if down_bytes == 0:
                return "∞" if up_bytes > 0 else "0"
            
            ratio = up_bytes / down_bytes
            return f"{ratio:.3f}"
        except Exception as e:
            logger.error(f"计算分享率失败: {str(e)}")
            return "0"

    def _parse_nexusphp_invite_page(self, site_name, html_content) -> dict:
        """
        解析NexusPHP邀请页面
        """
        result = {
                "invite_status": {
                    "can_invite": False,
                "reason": "",
                    "permanent_count": 0,
                "temporary_count": 0
            },
            "invitees": []
        }

        # 初始化BeautifulSoup对象
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # 检查是否有特殊标题，如"我的后宫"或"邀請系統"等
        special_title = False
        title_elem = soup.select_one('h1')
        if title_elem:
            title_text = title_elem.get_text().strip()
            if '后宫' in title_text or '後宮' in title_text or '邀請系統' in title_text or '邀请系统' in title_text:
                logger.info(f"站点 {site_name} 检测到特殊标题: {title_text}")
                special_title = True

        # 先检查info_block中的邀请信息
        info_block = soup.select_one('#info_block')
        if info_block:
            info_text = info_block.get_text()
            logger.info(f"站点 {site_name} 获取到info_block信息")
            
            # 识别邀请数量 - 查找邀请链接并获取数量
            invite_link = info_block.select_one('a[href*="invite.php"]')
            if invite_link:
                # 获取invite链接周围的文本
                parent_text = invite_link.parent.get_text() if invite_link.parent else ""
                
                # 尝试匹配常见格式: 数字(数字) 或 单个数字
                invite_pattern = re.compile(r'(?:邀请|探视权|invite).*?:?\s*(\d+)(?:\s*\((\d+)\))?', re.IGNORECASE)
                invite_match = invite_pattern.search(parent_text)
                
                if invite_match:
                    # 获取永久邀请数量
                    if invite_match.group(1):
                        result["invite_status"]["permanent_count"] = int(invite_match.group(1))
                    
                    # 如果有临时邀请数量
                    if len(invite_match.groups()) > 1 and invite_match.group(2):
                        result["invite_status"]["temporary_count"] = int(invite_match.group(2))
                    
                    logger.info(f"站点 {site_name} 解析到邀请数量: 永久={result['invite_status']['permanent_count']}, 临时={result['invite_status']['temporary_count']}")
                    
                    # 如果有邀请名额，初步判断为可邀请
                    if result["invite_status"]["permanent_count"] > 0 or result["invite_status"]["temporary_count"] > 0:
                        result["invite_status"]["can_invite"] = True
                        result["invite_status"]["reason"] = f"可用邀请数: 永久={result['invite_status']['permanent_count']}, 临时={result['invite_status']['temporary_count']}"
                else:
                    # 尝试在邀请链接后面的文本中查找数字
                    after_text = ""
                    next_sibling = invite_link.next_sibling
                    while next_sibling and not after_text.strip():
                        if isinstance(next_sibling, str):
                            after_text = next_sibling
                        next_sibling = next_sibling.next_sibling if hasattr(next_sibling, 'next_sibling') else None
                    
                    if after_text:
                        nums = re.findall(r'\d+', after_text)
                        if nums and len(nums) >= 1:
                            result["invite_status"]["permanent_count"] = int(nums[0])
                            if len(nums) >= 2:
                                result["invite_status"]["temporary_count"] = int(nums[1])
                            
                            logger.info(f"站点 {site_name} 从后续文本解析到邀请数量: 永久={result['invite_status']['permanent_count']}, 临时={result['invite_status']['temporary_count']}")
                            
                            # 如果有邀请名额，初步判断为可邀请
                            if result["invite_status"]["permanent_count"] > 0 or result["invite_status"]["temporary_count"] > 0:
                                result["invite_status"]["can_invite"] = True
                                result["invite_status"]["reason"] = f"可用邀请数: 永久={result['invite_status']['permanent_count']}, 临时={result['invite_status']['temporary_count']}"

        # 检查是否有标准的NexusPHP邀请页面结构
        invite_tables = soup.select('table.main > tbody > tr > td > table')

        # 如果页面没有invite_tables可能是未登录或者错误页面
        if not invite_tables:
            # 检查是否有其他表格
            any_tables = soup.select('table')
            if not any_tables:
                result["invite_status"]["reason"] = "页面解析错误，可能未登录或者站点结构特殊"
                logger.error(f"站点 {site_name} 邀请页面解析失败：没有找到任何表格")
                return result
            else:
                # 使用任意表格继续尝试
                invite_tables = any_tables

        # 检查是否存在"没有邀请权限"或"当前没有可用邀请名额"等提示
        error_patterns = [
            r"没有邀请权限",
            r"不能使用邀请",
            r"当前没有可用邀请名额",
            r"低于要求的等级",
            r"需要更高的用户等级",
            r"无法进行邀请注册",
            r"当前账户上限数已到",
            r"抱歉，目前没有开放注册",
            r"当前邀请注册人数已达上限",
            r"对不起",
            r"只有.*等级才能发送邀请",
            r"及以上.*才能发送邀请",
            r"\w+\s*or above can send invites"
        ]

        # 解析邀请权限状态
        page_text = soup.get_text()

        # 查找是否有邀请限制文本
        has_restriction = False
        restriction_reason = ""

        for pattern in error_patterns:
            matches = re.search(pattern, page_text, re.IGNORECASE)
            if matches:
                has_restriction = True
                restriction_reason = matches.group(0)
                result["invite_status"]["can_invite"] = False
                result["invite_status"]["reason"] = f"无法发送邀请: {restriction_reason}"
                logger.info(f"站点 {site_name} 发现邀请限制: {restriction_reason}")
                break

        # 检查是否存在发送邀请表单，这是最直接的判断依据
        invite_form = soup.select('form[action*="takeinvite.php"]')
        if invite_form:
            if not has_restriction:
                result["invite_status"]["can_invite"] = True
                if not result["invite_status"]["reason"]:
                    result["invite_status"]["reason"] = "存在邀请表单，可以发送邀请"
                logger.info(f"站点 {site_name} 存在邀请表单，可以发送邀请")
        else:
            # 如果没有找到takeinvite.php表单，再尝试更深入的检查
            
            # 1. 检查是否有"对不起"和等级限制的消息
            sorry_text = soup.find(text=re.compile(r'对不起|sorry'))
            if sorry_text:
                parent_element = None
                for parent in sorry_text.parents:
                    if parent.name in ['td', 'div', 'p']:
                        parent_element = parent
                        break
                
                if parent_element:
                    parent_text = parent_element.get_text()
                    # 检查是否包含等级限制或账户上限信息
                    if re.search(r'等级才|及以上|才可以|账户上限|上限数已到', parent_text):
                        result["invite_status"]["can_invite"] = False
                        result["invite_status"]["reason"] = parent_text.strip()
                        logger.info(f"站点 {site_name} 发现限制信息: {parent_text.strip()}")
                        has_restriction = True
            
            # 2. 尝试查找提交按钮，如果页面有提交按钮但不是在"对不起"消息中，可能有权限
            if not has_restriction:
                submit_buttons = soup.select('input[type="submit"]')
                if submit_buttons:
                    for button in submit_buttons:
                        # 获取按钮上下文
                        button_context = ""
                        parent = button.parent
                        while parent and parent.name != 'form':
                            button_context = parent.get_text()
                            parent = parent.parent
                        
                        # 检查按钮是否与邀请相关
                        if re.search(r'邀请|探视权|invite', button_context, re.IGNORECASE):
                            # 检查是否在表单内
                            is_in_form = False
                            for parent in button.parents:
                                if parent.name == 'form':
                                    is_in_form = True
                                    break
                            
                            if is_in_form:
                                result["invite_status"]["can_invite"] = True
                                if not result["invite_status"]["reason"]:
                                    result["invite_status"]["reason"] = "存在邀请提交按钮，可以发送邀请"
                                logger.info(f"站点 {site_name} 存在邀请提交按钮，可以发送邀请")
                                break

        # 查找可用邀请数量文本，常见格式有：
        # 1. "你有 X 个邀请名额"
        # 2. "您有 X 个邀请"
        invite_count_patterns = [
            r"你有\s*(\d+)\s*个邀请名额",
            r"您有\s*(\d+)\s*个邀请",
            r"你有\s*(\d+)\s*个邀请",
            r"邀请.*?(\d+).*?\((\d+)\)",  # 匹配 "邀请: 1(0)" 格式
            r"剩余邀请\s*:\s*(\d+)",
            r"invites? left:?\s*(\d+)"
        ]

        # 如果之前没有找到邀请数量，再次尝试从页面文本中查找
        if result["invite_status"]["permanent_count"] == 0:
            # 检查是否存在永久邀请数量
            for pattern in invite_count_patterns:
                matches = re.search(pattern, page_text, re.IGNORECASE)
                if matches:
                    invite_count = int(matches.group(1))
                    result["invite_status"]["permanent_count"] = invite_count
                    
                    # 如果有第二个捕获组，可能是临时邀请
                    if len(matches.groups()) > 1 and matches.group(2):
                        try:
                            temp_count = int(matches.group(2))
                            result["invite_status"]["temporary_count"] = temp_count
                        except (ValueError, TypeError):
                            pass

                    # 如果有邀请名额且没有发现限制，则可以邀请
                    if invite_count > 0 and not has_restriction:
                        result["invite_status"]["can_invite"] = True
                        result["invite_status"]["reason"] = f"可用邀请数: {invite_count}"
                        logger.info(f"站点 {site_name} 可用邀请数: {invite_count}")
                    break

        # 如果找不到永久邀请，也看看是否有临时邀请
        temp_invite_patterns = [
            r"临时邀请\s*:\s*(\d+)",
            r"限时邀请\s*:\s*(\d+)"
        ]

        if result["invite_status"]["temporary_count"] == 0:
            for pattern in temp_invite_patterns:
                matches = re.search(pattern, page_text, re.IGNORECASE)
                if matches:
                    temp_count = int(matches.group(1))
                    result["invite_status"]["temporary_count"] = temp_count

                    # 如果有临时邀请且没有其他限制，则可以邀请
                    if temp_count > 0 and not has_restriction and not result["invite_status"]["can_invite"]:
                        result["invite_status"]["can_invite"] = True
                        result["invite_status"]["reason"] = f"可用临时邀请数: {temp_count}"
                        logger.info(f"站点 {site_name} 可用临时邀请数: {temp_count}")
                    break

        # 优先查找带有border属性的表格，这通常是用户列表表格
        invitee_tables = soup.select('table[border="1"]')
        
        # 如果没找到，再尝试标准表格结构
        if not invitee_tables:
            invitee_tables = soup.select('table.main table.torrents')
            
            # 如果还没找到，尝试查找任何可能包含用户数据的表格
            if not invitee_tables:
                all_tables = soup.select('table')
                # 过滤掉小表格
                invitee_tables = [table for table in all_tables 
                                 if len(table.select('tr')) > 2]
        
        # 处理找到的表格
        for table in invitee_tables:
            # 获取表头
            header_row = table.select_one('tr')
            if not header_row:
                continue
                
            headers = []
            header_cells = header_row.select('td.colhead, th.colhead, td, th')
            for cell in header_cells:
                headers.append(cell.get_text(strip=True))
                
            # 检查是否是用户表格 - 查找关键列头
            if not any(keyword in ' '.join(headers).lower() for keyword in 
                      ['用户名', '邮箱', 'email', '分享率', 'ratio', 'username']):
                continue
                
            logger.info(f"站点 {site_name} 找到后宫用户表，表头: {headers}")
            
            # 解析表格行
            rows = table.select('tr:not(:first-child)')
            for row in rows:
                cells = row.select('td')
                if not cells or len(cells) < 3:  # 至少需要3列才可能是有效数据
                    continue
                    
                invitee = {}
                
                # 检查行类和禁用标记
                row_classes = row.get('class', [])
                is_banned = any(cls in ['rowbanned', 'banned', 'disabled'] 
                               for cls in row_classes)
                
                # 查找禁用图标
                disabled_img = row.select_one('img.disabled, img[alt="Disabled"]')
                if disabled_img:
                    is_banned = True
                
                # 解析各列数据
                for idx, cell in enumerate(cells):
                    if idx >= len(headers):
                        break
                        
                    header = headers[idx].lower()
                    cell_text = cell.get_text(strip=True)
                    
                    # 用户名和链接
                    if any(keyword in header for keyword in ['用户名', 'username', '名字', 'user']):
                        username_link = cell.select_one('a')
                        if username_link:
                            invitee["username"] = username_link.get_text(strip=True)
                            href = username_link.get('href', '')
                            site_info = self.sites.get_indexer(site_name)
                            site_url = site_info.get("url", "") if site_info else ""
                            invitee["profile_url"] = urljoin(site_url, href) if href else ""
                        else:
                            invitee["username"] = cell_text
                    
                    # 邮箱
                    elif any(keyword in header for keyword in ['邮箱', 'email', '电子邮件', 'mail']):
                        invitee["email"] = cell_text
                    
                    # 启用状态 - 直接检查yes/no
                    elif any(keyword in header for keyword in ['启用', '狀態', 'enabled', 'status']):
                        status_text = cell_text.lower()
                        if status_text == 'no' or '禁' in status_text or 'disabled' in status_text or 'banned' in status_text:
                            invitee["enabled"] = "No"
                            is_banned = True
                        else:
                            invitee["enabled"] = "Yes"
                    
                    # 上传量
                    elif any(keyword in header for keyword in ['上传', '上傳', 'uploaded', 'upload']):
                        invitee["uploaded"] = cell_text
                    
                    # 下载量
                    elif any(keyword in header for keyword in ['下载', '下載', 'downloaded', 'download']):
                        invitee["downloaded"] = cell_text
                    
                    # 分享率 - 特别处理∞、Inf.等情况
                    elif any(keyword in header for keyword in ['分享率', '分享', 'ratio']):
                        # 标准化分享率表示
                        ratio_text = cell_text
                        if ratio_text == '---' or not ratio_text:
                            ratio_text = '0'
                        elif ratio_text.lower() in ['inf.', 'inf', '无限']:
                            ratio_text = '∞'
                            
                        invitee["ratio"] = ratio_text
                        
                        # 计算分享率数值
                        try:
                            if ratio_text == '∞':
                                invitee["ratio_value"] = 1e20
                            else:
                                # 替换逗号为点
                                normalized_ratio = ratio_text.replace(',', '.')
                                invitee["ratio_value"] = float(normalized_ratio)
                        except (ValueError, TypeError):
                            invitee["ratio_value"] = 0
                            logger.warning(f"无法解析分享率: {ratio_text}")
                    
                    # 做种数
                    elif any(keyword in header for keyword in ['做种数', '做種數', 'seeding', 'seed']):
                        invitee["seeding"] = cell_text
                    
                    # 做种体积
                    elif any(keyword in header for keyword in ['做种体积', '做種體積', 'seeding size']):
                        invitee["seeding_size"] = cell_text
                    
                    # 做种时间/魔力值
                    elif any(keyword in header for keyword in ['做种时间', '做種時間', 'seed time']):
                        invitee["seed_time"] = cell_text
                    
                    # 后宫加成/魔力值
                    elif any(keyword in header for keyword in ['后宫加成', '魔力值', '後宮加成', 'bonus']):
                        invitee["seed_bonus"] = cell_text
                    
                    # 最后活动/做种时间
                    elif any(keyword in header for keyword in ['最后活动', '最後活動', 'last seen', 'last activity']):
                        invitee["last_seen"] = cell_text
                    
                    # 做种时魔（纯做种时魔）
                    elif any(keyword in header for keyword in ['做种时魔', '純做種時魔', 'seed bonus', '纯做种时魔', '当前纯做种时魔']):
                        invitee["seed_magic"] = cell_text
                    
                    # 最后做种汇报时间
                    elif any(keyword in header for keyword in ['最后做种汇报时间', '最後做種匯報時間', 'last seed report', '做种汇报']):
                        invitee["last_seed_report"] = cell_text
                    
                    # 用户状态
                    elif any(keyword in header for keyword in ['状态', '狀態', 'status']) and "enabled" not in invitee:
                        invitee["status"] = cell_text
                        
                        # 根据状态文本判断是否被禁用
                        status_lower = cell_text.lower()
                        if any(ban_word in status_lower for ban_word in ['banned', 'disabled', '禁止', '封禁']):
                            invitee["enabled"] = "No"
                            is_banned = True
                
                # 如果尚未设置enabled状态，根据行类或图标判断
                if "enabled" not in invitee:
                    invitee["enabled"] = "No" if is_banned else "Yes"
                
                # 设置状态字段(如果尚未设置)
                if "status" not in invitee:
                    invitee["status"] = "已禁用" if is_banned else "已确认"
                
                # 计算分享率健康状态
                if "ratio_value" in invitee:
                    if invitee["ratio_value"] >= 1e20:
                        invitee["ratio_health"] = "excellent"
                    elif invitee["ratio_value"] >= 1.0:
                        invitee["ratio_health"] = "good"
                    elif invitee["ratio_value"] >= 0.5:
                        invitee["ratio_health"] = "warning"
                    else:
                        invitee["ratio_health"] = "danger"
                
                # 将解析到的用户添加到列表中
                if invitee.get("username"):
                            result["invitees"].append(invitee)

            # 如果已找到用户数据，跳出循环
            if result["invitees"]:
                logger.info(f"站点 {site_name} 已解析 {len(result['invitees'])} 个后宫成员")
                break

        # 如果没有解析到数据，可能是特殊页面结构，尝试处理
        if special_title and not result["invitees"]:
            logger.info(f"站点 {site_name} 尝试处理特殊页面结构")
            
            # 直接查找border="1"的表格，这通常是用户列表表格
            border_tables = soup.select('table[border="1"]')
            if border_tables:
                # 选取第一个border="1"表格
                table = border_tables[0]
                
                # 获取表头
                header_row = table.select_one('tr')
                if header_row:
                    # 获取所有表头单元格
                    header_cells = header_row.select('td.colhead, th.colhead, td, th')
                    headers = [cell.get_text(strip=True).lower() for cell in header_cells]
                    
                    logger.info(f"站点 {site_name} 找到用户表格，表头: {headers}")
                    
                    # 找到所有数据行（跳过表头行）
                    data_rows = table.select('tr.rowfollow')
                    
                    # 清空已有数据，避免重复
                    result["invitees"] = []
                    processed_usernames = set()  # 用于跟踪已处理的用户名，避免重复
                    
                    for row in data_rows:
                        cells = row.select('td')
                        if len(cells) < len(headers):
                            continue
                        
                        invitee = {}
                        username = ""
                        is_banned = False
                        
                        # 检查行类和禁用标记
                        row_classes = row.get('class', [])
                        if isinstance(row_classes, list) and any(cls in ['rowbanned', 'banned'] for cls in row_classes):
                            is_banned = True
                        
                        # 逐列解析数据
                        for idx, header in enumerate(headers):
                            if idx >= len(cells):
                                break
                            
                            cell = cells[idx]
                            cell_text = cell.get_text(strip=True)
                            
                            # 用户名列（通常是第一列）
                            if idx == 0 or any(kw in header for kw in ['用户名', '用戶名', 'username', 'user']):
                                username_link = cell.select_one('a')
                                disabled_img = cell.select_one('img.disabled, img[alt="Disabled"]')
                                
                                if disabled_img:
                                    is_banned = True
                                
                                if username_link:
                                    username = username_link.get_text(strip=True)
                                    invitee["username"] = username
                                    
                                    # 处理可能在用户名中附带的Disabled文本
                                    if "Disabled" in cell.get_text():
                                        is_banned = True
                                    
                                    # 获取用户个人页链接
                                    href = username_link.get('href', '')
                                    site_info = self.sites.get_indexer(site_name)
                                    site_url = site_info.get("url", "") if site_info else ""
                                    invitee["profile_url"] = urljoin(site_url, href) if href else ""
                                else:
                                    username = cell_text
                                    invitee["username"] = username
                            
                            # 邮箱列
                            elif any(kw in header for kw in ['郵箱', '邮箱', 'email', 'mail']):
                                invitee["email"] = cell_text
                            
                            # 启用状态列
                            elif any(kw in header for kw in ['啟用', '启用', 'enabled']):
                                status_text = cell_text.lower()
                                if status_text == 'no' or '禁' in status_text:
                                    invitee["enabled"] = "No"
                                    is_banned = True
                                else:
                                    invitee["enabled"] = "Yes"
                            
                            # 上传量列
                            elif any(kw in header for kw in ['上傳', '上传', 'uploaded', 'upload']):
                                invitee["uploaded"] = cell_text
                            
                            # 下载量列
                            elif any(kw in header for kw in ['下載', '下载', 'downloaded', 'download']):
                                invitee["downloaded"] = cell_text
                            
                            # 分享率列 - 特别处理∞、Inf.等情况
                            elif any(kw in header for kw in ['分享率', '分享比率', 'ratio']):
                                ratio_text = cell_text
                                
                                # 处理特殊分享率表示
                                if ratio_text.lower() in ['inf.', 'inf', '∞', 'infinite', '无限']:
                                    invitee["ratio"] = "∞"
                                    invitee["ratio_value"] = 1e20
                                elif ratio_text == '---' or not ratio_text:
                                    invitee["ratio"] = "0"
                                    invitee["ratio_value"] = 0
                                else:
                                    # 获取font标签内的文本，如果存在
                                    font_tag = cell.select_one('font')
                                    if font_tag:
                                        ratio_text = font_tag.get_text(strip=True)
                                    
                                    invitee["ratio"] = ratio_text
                                    
                                    # 尝试解析为浮点数
                                    try:
                                        # 替换逗号为点
                                        normalized_ratio = ratio_text.replace(',', '.')
                                        invitee["ratio_value"] = float(normalized_ratio)
                                    except (ValueError, TypeError):
                                        logger.warning(f"无法解析分享率: {ratio_text}")
                                        invitee["ratio_value"] = 0
                            
                            # 做种数列
                            elif any(kw in header for kw in ['做種數', '做种数', 'seeding', 'seeds']):
                                invitee["seeding"] = cell_text
                            
                            # 做种体积列
                            elif any(kw in header for kw in ['做種體積', '做种体积', 'seeding size', 'seed size']):
                                invitee["seeding_size"] = cell_text
                            
                            # 当前纯做种时魔列
                            elif any(kw in header for kw in ['純做種時魔', '当前纯做种时魔', '纯做种时魔', 'seed magic']):
                                invitee["seed_magic"] = cell_text
                            
                            # 后宫加成列
                            elif any(kw in header for kw in ['後宮加成', '后宫加成', 'bonus']):
                                invitee["seed_bonus"] = cell_text
                            
                            # 最后做种汇报时间列
                            elif any(kw in header for kw in ['最後做種匯報時間', '最后做种汇报时间', '最后做种报告', 'last seed']):
                                invitee["last_seed_report"] = cell_text
                            
                            # 状态列
                            elif any(kw in header for kw in ['狀態', '状态', 'status']):
                                invitee["status"] = cell_text
                                
                                # 根据状态判断是否禁用
                                status_lower = cell_text.lower()
                                if any(ban_word in status_lower for ban_word in ['banned', 'disabled', '禁止', '禁用', '封禁']):
                                    is_banned = True
                        
                        # 如果用户名不为空且未处理过
                        if username and username not in processed_usernames:
                            processed_usernames.add(username)
                            
                            # 设置启用状态（如果尚未设置）
                            if "enabled" not in invitee:
                                invitee["enabled"] = "No" if is_banned else "Yes"
                            
                            # 设置状态（如果尚未设置）
                            if "status" not in invitee:
                                invitee["status"] = "已禁用" if is_banned else "已確認"
                            
                            # 计算分享率健康状态
                            if "ratio_value" in invitee:
                                if invitee["ratio_value"] >= 1e20:
                                    invitee["ratio_health"] = "excellent"
                                elif invitee["ratio_value"] >= 1.0:
                                    invitee["ratio_health"] = "good"
                                elif invitee["ratio_value"] >= 0.5:
                                    invitee["ratio_health"] = "warning"
                                else:
                                    invitee["ratio_health"] = "danger"
                            
                            # 将用户数据添加到结果中
                            if invitee.get("username"):
                                result["invitees"].append(invitee.copy())
                    
                    # 记录解析结果
                    if result["invitees"]:
                        logger.info(f"站点 {site_name} 从特殊格式表格解析到 {len(result['invitees'])} 个后宫成员")
            
            # 检查邀请权限
            form_disabled = soup.select_one('input[disabled][value*="貴賓 或以上等級才可以"]')
            if form_disabled:
                disabled_text = form_disabled.get('value', '')
                result["invite_status"]["can_invite"] = False
                result["invite_status"]["reason"] = disabled_text
                logger.info(f"站点 {site_name} 邀请按钮被禁用: {disabled_text}")

        return result

    def _parse_mteam_invite_page(self, html_content: str, stats_data: dict = None) -> dict:
        """
        解析M-Team邀请页面
        """
        try:
            result = {
                "invitees": [],
                "sent_invites": [],
                "temp_invites": [],
                "invite_status": {
                    "can_invite": False,
                    "permanent_count": 0,
                    "temporary_count": 0,
                    "reason": "",
                    "stats": stats_data or {}
                }
            }

            # 尝试从页面中提取JSON数据
            json_match = re.search(
                r'window\.__INITIAL_STATE__\s*=\s*({.*?});', html_content)
            if json_match:
                try:
                    data = json.loads(json_match.group(1))
                    
                    # 解析邀请名额
                    if "inviteQuota" in data:
                        result["invite_status"].update({
                            "permanent_count": data["inviteQuota"].get("permanent", 0),
                            "temporary_count": data["inviteQuota"].get("temporary", 0),
                            "can_invite": data["inviteQuota"].get("permanent", 0) > 0 or 
                                        data["inviteQuota"].get("temporary", 0) > 0
                        })

                    # 解析被邀请人列表
                    if "invitees" in data:
                        for invitee in data["invitees"]:
                            result["invitees"].append({
                                "username": invitee.get("username", ""),
                                "email": invitee.get("email", ""),
                                "uploaded": self._format_size(invitee.get("uploaded", 0)),
                                "downloaded": self._format_size(invitee.get("downloaded", 0)),
                                # 转换为字符串
                                "ratio": str(round(float(invitee.get("ratio", 0)), 2)),
                                "status": invitee.get("status", ""),
                                "profile_url": f"/profile/detail/{invitee.get('uid', '')}"
                            })

                    # 解析已发送邀请
                    if "sentInvites" in data:
                        for invite in data["sentInvites"]:
                            result["sent_invites"].append({
                                "email": invite.get("email", ""),
                                "send_date": invite.get("created_at", ""),
                                "status": invite.get("status", ""),
                                "hash": invite.get("hash", "")
                            })

                except json.JSONDecodeError:
                    logger.error("解析M-Team页面JSON数据失败")

            return result

        except Exception as e:
            logger.error(f"解析M-Team邀请页面失败: {str(e)}")
            return {}

    def _format_size(self, size_bytes: int) -> str:
        """
        格式化文件大小
        """
        try:
            for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
                if size_bytes < 1024.0:
                    return f"{size_bytes:.2f} {unit}"
                size_bytes /= 1024.0
            return f"{size_bytes:.2f} PB"
        except:
            return "0 B"

    def get_invitees(self, apikey: str = None, force_update: bool = False) -> dict:
        """
        获取所有站点的邀请数据API接口
        """
        if apikey and apikey != settings.API_TOKEN:
            return {"code": 1, "message": "API令牌错误!"}
            
        try:
            # 确保从配置文件加载最新数据
            self._sync_from_file()
            
            # 检查是否选择了站点
            if not self._site_ids:
                return {"code": 1, "message": "未选择任何站点，请先在配置中选择要管理的站点"}

            # 获取当前缓存数据
            cached_data = self._config.get("cached_data", {})

            # 如果强制更新或者没有缓存数据，则重新获取数据
            if force_update or not cached_data:
                result = self.refresh_all_sites()
                if not result or result.get("error") > 0:
                    logger.warning(f"部分站点数据刷新失败: {result}")

                # 重新获取缓存数据
                cached_data = self._config.get("cached_data", {})

            # 判断结果是否有效
            if not cached_data:
                return {"code": 1, "message": "暂无数据，请先刷新数据"}

            # 统计各站点最后更新时间
            update_times = []
            for site_data in cached_data.values():
                if site_data and "last_update" in site_data:
                    update_times.append(site_data["last_update"])

            # 找出最近的更新时间
            last_update = max(update_times) if update_times else 0

            return {
                "code": 0,
                "message": "获取成功",
                "last_update": last_update,
                "data": cached_data
            }

        except Exception as e:
            logger.error(f"获取邀请数据失败: {str(e)}")
            return {"code": 1, "message": f"获取邀请数据失败: {str(e)}"}

    def refresh_data(self, apikey: str = None) -> dict:
        """
        强制刷新所有站点数据API接口
        """
        if apikey and apikey != settings.API_TOKEN:
            return {"code": 1, "message": "API令牌错误!"}

        try:
            # 调用refresh_all_sites方法刷新数据
            result = self.refresh_all_sites()

            if result.get("success", 0) > 0:
                # 获取最新的更新时间
                cached_data = self._config.get("cached_data", {})
                update_times = []
                for site_data in cached_data.values():
                    if site_data and "last_update" in site_data:
                        update_times.append(site_data["last_update"])

                last_update = max(update_times) if update_times else 0

                return {
                    "code": 0,
                    "message": f"数据刷新成功: {result.get('success')}个站点, 失败: {result.get('error')}个站点",
                    "data": {
                        "last_update": last_update,
                        "site_count": len(cached_data),
                        "success": result.get("success", 0),
                        "error": result.get("error", 0)
                    }
                }
            else:
                return {"code": 1, "message": "数据刷新失败，没有成功刷新的站点"}
            
        except Exception as e:
            logger.error(f"强制刷新数据失败: {str(e)}")
            return {"code": 1, "message": f"强制刷新数据失败: {str(e)}"}

    def _save_config(self):
        """
        保存配置到文件
        """
        try:
            # 更新配置
            self._config.update({
                "cron": self._cron,
                "onlyonce": self._onlyonce,
                "site_ids": self._site_ids
            })
            # 同步到文件
            self._sync_to_file()
            return True
        except Exception as e:
            logger.error(f"保存配置失败: {str(e)}")
            return False

    def refresh_all_sites(self):
        """
        刷新所有站点数据
        """
        # 获取所有选中的站点
        logger.info(f"开始刷新所有站点后宫数据...")
        # 检查是否选择了站点
        if not self._site_ids:
            logger.error("未选择任何站点，无法刷新数据")
            self.post_message(
                mtype=NotificationType.SiteMessage,
                title="后宫管理系统",
                text="未选择任何站点，请在配置中选择要管理的站点"
            )
            return

        # 准备有效站点列表
        valid_sites = []
        for site_id in self._site_ids:
            # 使用正确的方法获取站点信息
            site_info = None
            for indexer in self.sites.get_indexers():
                if str(indexer.get("id")) == str(site_id):
                    site_info = indexer
                    break
                    
            if not site_info:
                logger.warning(f"站点ID {site_id} 配置不存在，跳过")
                continue
                
            site_name = site_info.get('name')
            if not site_name:
                logger.warning(f"站点ID {site_id} 配置无效，跳过")
                continue
                
            # 不再检查站点状态，直接使用配置中的站点
            valid_sites.append(site_name)
            
        if not valid_sites:
            logger.error("没有有效的站点配置，无法刷新数据")
            self.post_message(
                mtype=NotificationType.SiteMessage,
                title="后宫管理系统",
                text="没有有效的站点配置，请检查站点状态"
            )
            return

        logger.info(f"将刷新以下站点数据: {', '.join(valid_sites)}")

        # 初始化缓存数据字典（如果不存在）
        if "cached_data" not in self._config:
            self._config["cached_data"] = {}

        # 依次刷新每个站点
        success_count = 0
        error_count = 0
        for site_name in valid_sites:
            try:
                logger.info(f"开始获取站点 {site_name} 的后宫数据...")
                site_data = self._get_site_invite_data(site_name)
                if site_data and not site_data.get("error"):
                    # 更新缓存数据
                    self._config["cached_data"][site_name] = {
                        "data": site_data,
                        "last_update": int(time.time())
                    }
                    self._sync_to_file()
                    success_count += 1
                    logger.info(
                        f"站点 {site_name} 数据刷新成功，已邀请 {len(site_data.get('invitees', []))} 人")
                else:
                    error_message = site_data.get("error", "未知错误")
                    logger.error(f"站点 {site_name} 数据刷新失败: {error_message}")
                    error_count += 1
            except Exception as e:
                logger.error(f"站点 {site_name} 数据刷新出错: {str(e)}")
                error_count += 1

        # 完成更新
        message = f"刷新完成: 成功 {success_count} 个站点, 失败 {error_count} 个站点"
        logger.info(message)
        self.post_message(
            mtype=NotificationType.SiteMessage,
            title="后宫管理系统",
            text=message
        )
        return {"success": success_count, "error": error_count}

    def scheduler_handler(self, event=None):
        """
        定时任务处理
        """
        logger.info(f"后宫管理系统定时任务开始执行：{event}")
        # 强制刷新数据
        self.refresh_all_sites()
        logger.info("后宫管理系统定时任务完成")
        return True

    @staticmethod
    def get_api_handlers():
        """
        获取API接口
        """
        return {
            "/get_invitees": {"func": nexusinvitee.get_invitees, "methods": ["GET"], "desc": "获取所有站点邀请数据"},
            "/refresh": {"func": nexusinvitee.refresh_data, "methods": ["GET"], "desc": "强制刷新站点数据"}
        }

    def get_service(self) -> List[Dict[str, Any]]:
        """
        注册插件公共服务
        """
        if self._enabled and self._cron:
            try:
                # 检查是否为5位cron表达式
                if str(self._cron).strip().count(" ") == 4:
                    return [{
                        "id": "nexusinvitee",
                        "name": "后宫管理系统",
                        "trigger": CronTrigger.from_crontab(self._cron),
                        "func": self.refresh_all_sites,
                        "kwargs": {}
                    }]
                else:
                    logger.error("cron表达式格式错误")
                    return []
            except Exception as err:
                logger.error(f"定时任务配置错误：{str(err)}")
                return []
        return []


# 插件类导出
plugin_class = nexusinvitee 
