"""
标准NexusPHP站点处理
"""
import re
from typing import Dict, Any, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from app.log import logger
from plugins.nexusinvitee.sites import _ISiteHandler


class NexusPhpHandler(_ISiteHandler):
    """
    标准NexusPHP站点处理类
    """
    # 站点类型标识
    site_schema = "nexusphp"
    
    @classmethod
    def match(cls, site_url: str) -> bool:
        """
        判断是否匹配NexusPHP站点
        :param site_url: 站点URL
        :return: 是否匹配
        """
        # 排除已知的特殊站点
        special_sites = ["m-team", "totheglory", "hdchina", "butterfly", "dmhy", "蝶粉"]
        if any(site in site_url.lower() for site in special_sites):
            return False
            
        # 标准NexusPHP站点的URL特征
        nexus_features = [
            "php",                  # 大多数NexusPHP站点URL包含php
            "nexus",                # 部分站点URL中包含nexus
            "agsvpt",               # 红豆饭

            "audiences",            # 观众
            "hdpt",                 # HD盘他
            "wintersakura",         # 冬樱

            "hdmayi",               # 蚂蚁
            "u2.dmhy",              # U2
            "hddolby",              # 杜比
            "hdarea",               # 高清地带
            "pt.soulvoice",         # 聆音

            "ptsbao",               # PT书包
            "hdhome",               # HD家园
            "hdatmos",              # 阿童木
            "1ptba",                # 1PT
            "keepfrds",             # 朋友
            "moecat",               # 萌猫
            "springsunday"          # 春天
        ]
        
        # 如果URL中包含任何一个NexusPHP特征，则认为是NexusPHP站点
        site_url_lower = site_url.lower()
        for feature in nexus_features:
            if feature in site_url_lower:
                logger.info(f"匹配到NexusPHP站点特征: {feature}")
                return True
                
        # 如果没有匹配到特征，但URL中包含PHP，也视为可能的NexusPHP站点
        if "php" in site_url_lower:
            logger.info(f"URL中包含PHP，可能是NexusPHP站点: {site_url}")
            return True
            
        return False
    
    def parse_invite_page(self, site_info: Dict[str, Any], session: requests.Session) -> Dict[str, Any]:
        """
        解析NexusPHP站点邀请页面
        :param site_info: 站点信息
        :param session: 已配置好的请求会话
        :return: 解析结果
        """
        site_name = site_info.get("name", "")
        site_url = site_info.get("url", "")
        
        result = {
            "invite_status": {
                "can_invite": False,
                "reason": "",
                "permanent_count": 0,
                "temporary_count": 0,
                "bonus": 0,  # 添加魔力值
                "permanent_invite_price": 0,  # 添加永久邀请价格
                "temporary_invite_price": 0   # 添加临时邀请价格
            },
            "invitees": []
        }
        
        try:
            # 获取用户ID
            user_id = self._get_user_id(session, site_url)
            if not user_id:
                logger.error(f"站点 {site_name} 无法获取用户ID")
                result["invite_status"]["reason"] = "无法获取用户ID，请检查站点Cookie是否有效"
                return result
            
            # 获取邀请页面 - 从第一页开始
            invite_url = urljoin(site_url, f"invite.php?id={user_id}")
            response = session.get(invite_url, timeout=(10, 30))
            response.raise_for_status()
            
            # 解析邀请页面
            invite_result = self._parse_nexusphp_invite_page(site_name, response.text)
            
            # 检查第一页后宫成员数量，如果少于50人，则不再翻页
            if len(invite_result["invitees"]) < 50:
                logger.info(f"站点 {site_name} 首页后宫成员数量少于50人({len(invite_result['invitees'])}人)，不再查找后续页面")
                # 如果成功解析到后宫成员，记录总数
                if invite_result["invitees"]:
                    logger.info(f"站点 {site_name} 共解析到 {len(invite_result['invitees'])} 个后宫成员")
                return invite_result
            
            # 尝试获取更多页面的后宫成员
            next_page = 1  # 从第二页开始，因为第一页已经解析过了
            max_pages = 100  # 防止无限循环
            
            # 继续获取后续页面，直到没有更多数据或达到最大页数
            while next_page < max_pages:
                next_page_url = urljoin(site_url, f"invite.php?id={user_id}&menu=invitee&page={next_page}")
                logger.info(f"站点 {site_name} 正在获取第 {next_page+1} 页后宫成员数据: {next_page_url}")
                
                try:
                    next_response = session.get(next_page_url, timeout=(10, 30))
                    next_response.raise_for_status()
                    
                    # 解析下一页数据
                    next_page_result = self._parse_nexusphp_invite_page(site_name, next_response.text, is_next_page=True)
                    
                    # 如果没有找到任何后宫成员，说明已到达最后一页
                    if not next_page_result["invitees"]:
                        logger.info(f"站点 {site_name} 第 {next_page+1} 页没有后宫成员数据，停止获取")
                        break
                    
                    # 如果当前页面后宫成员少于50人，默认认为没有下一页，避免错误进入下一页
                    if len(next_page_result["invitees"]) < 50:
                        logger.info(f"站点 {site_name} 第 {next_page+1} 页后宫成员数量少于50人({len(next_page_result['invitees'])}人)，默认没有下一页")
                        # 将当前页数据合并到结果中后退出循环
                        invite_result["invitees"].extend(next_page_result["invitees"])
                        logger.info(f"站点 {site_name} 第 {next_page+1} 页解析到 {len(next_page_result['invitees'])} 个后宫成员")
                        break
                    
                    # 将下一页的后宫成员添加到结果中
                    invite_result["invitees"].extend(next_page_result["invitees"])
                    logger.info(f"站点 {site_name} 第 {next_page+1} 页解析到 {len(next_page_result['invitees'])} 个后宫成员")
                    
                    # 继续下一页
                    next_page += 1
                    
                except Exception as e:
                    logger.warning(f"站点 {site_name} 获取第 {next_page+1} 页数据失败: {str(e)}")
                    break
            
            # 获取魔力值商店页面，尝试解析邀请价格
            try:
                bonus_url = urljoin(site_url, "mybonus.php")
                bonus_response = session.get(bonus_url, timeout=(10, 30))
                if bonus_response.status_code == 200:
                    # 解析魔力值和邀请价格
                    bonus_data = self._parse_bonus_shop(site_name, bonus_response.text)
                    # 更新邀请状态
                    invite_result["invite_status"]["bonus"] = bonus_data["bonus"]
                    invite_result["invite_status"]["permanent_invite_price"] = bonus_data["permanent_invite_price"]
                    invite_result["invite_status"]["temporary_invite_price"] = bonus_data["temporary_invite_price"]
                    
                    # 判断是否可以购买邀请
                    if bonus_data["bonus"] > 0:
                        # 计算可购买的邀请数量
                        can_buy_permanent = 0
                        can_buy_temporary = 0
                        
                        if bonus_data["permanent_invite_price"] > 0:
                            can_buy_permanent = int(bonus_data["bonus"] / bonus_data["permanent_invite_price"])
                        
                        if bonus_data["temporary_invite_price"] > 0:
                            can_buy_temporary = int(bonus_data["bonus"] / bonus_data["temporary_invite_price"])
                            
                        # 更新邀请状态的原因字段
                        if invite_result["invite_status"]["reason"] and not invite_result["invite_status"]["can_invite"]:
                            # 如果有原因且不能邀请
                            if can_buy_temporary > 0 or can_buy_permanent > 0:
                                invite_method = ""
                                if can_buy_temporary > 0 and bonus_data["temporary_invite_price"] > 0:
                                    invite_method += f"临时邀请({can_buy_temporary}个,{bonus_data['temporary_invite_price']}魔力/个)"
                                
                                if can_buy_permanent > 0 and bonus_data["permanent_invite_price"] > 0:
                                    if invite_method:
                                        invite_method += ","
                                    invite_method += f"永久邀请({can_buy_permanent}个,{bonus_data['permanent_invite_price']}魔力/个)"
                                
                                if invite_method:
                                    invite_result["invite_status"]["reason"] += f"，但您的魔力值({bonus_data['bonus']})可购买{invite_method}"
                                    # 如果可以购买且没有现成邀请，也视为可邀请
                                    if invite_result["invite_status"]["permanent_count"] == 0 and invite_result["invite_status"]["temporary_count"] == 0:
                                        invite_result["invite_status"]["can_invite"] = True
                        else:
                            # 如果没有原因或者已经可以邀请
                            if can_buy_temporary > 0 or can_buy_permanent > 0:
                                invite_method = ""
                                if can_buy_temporary > 0 and bonus_data["temporary_invite_price"] > 0:
                                    invite_method += f"临时邀请({can_buy_temporary}个,{bonus_data['temporary_invite_price']}魔力/个)"
                                
                                if can_buy_permanent > 0 and bonus_data["permanent_invite_price"] > 0:
                                    if invite_method:
                                        invite_method += ","
                                    invite_method += f"永久邀请({can_buy_permanent}个,{bonus_data['permanent_invite_price']}魔力/个)"
                                
                                if invite_method and invite_result["invite_status"]["reason"]:
                                    invite_result["invite_status"]["reason"] += f"，魔力值({bonus_data['bonus']})可购买{invite_method}"
                    
            except Exception as e:
                logger.warning(f"站点 {site_name} 解析魔力值商店失败: {str(e)}")
            
            # 访问发送邀请页面，这是判断权限的关键
            send_invite_url = urljoin(site_url, f"invite.php?id={user_id}&type=new")
            try:
                send_response = session.get(send_invite_url, timeout=(10, 30))
                send_response.raise_for_status()
                
                # 解析发送邀请页面
                send_soup = BeautifulSoup(send_response.text, 'html.parser')
                
                # 检查是否有takeinvite.php表单 - 最直接的权限判断
                invite_form = send_soup.select('form[action*="takeinvite.php"]')
                if invite_form:
                    # 确认有表单，权限正常
                    invite_result["invite_status"]["can_invite"] = True
                    if not invite_result["invite_status"]["reason"]:
                        invite_result["invite_status"]["reason"] = "可以发送邀请"
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
                            
                            invite_result["invite_status"]["can_invite"] = False
                            invite_result["invite_status"]["reason"] = restriction_text
                            logger.info(f"站点 {site_name} 有邀请限制: {restriction_text}")
                
            except requests.exceptions.RequestException as e:
                logger.warning(f"访问站点发送邀请页面失败，使用默认权限判断: {str(e)}")
            
            # 如果成功解析到后宫成员，记录总数
            if invite_result["invitees"]:
                logger.info(f"站点 {site_name} 共解析到 {len(invite_result['invitees'])} 个后宫成员")
            
            return invite_result
            
        except Exception as e:
            logger.error(f"解析站点 {site_name} 邀请页面失败: {str(e)}")
            result["invite_status"]["reason"] = f"解析邀请页面失败: {str(e)}"
            return result
    
    def _parse_nexusphp_invite_page(self, site_name: str, html_content: str, is_next_page: bool = False) -> Dict[str, Any]:
        """
        解析NexusPHP邀请页面HTML内容
        :param site_name: 站点名称
        :param html_content: HTML内容
        :param is_next_page: 是否是翻页内容，如果是则只提取后宫成员数据
        :return: 解析结果
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
        
        # 如果不是翻页内容，解析邀请状态
        if not is_next_page:
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
                    logger.debug(f"站点 {site_name} 原始邀请文本: {parent_text}")
                    
                    # 更精确的邀请解析模式：处理两种情况
                    # 1. 只有永久邀请: "邀请 [发送]: 0"
                    # 2. 永久+临时邀请: "探视权 [发送]: 1(0)"
                    invite_pattern = re.compile(r'(?:邀请|探视权|invite|邀請|查看权|查看權).*?(?:\[.*?\]|发送|查看).*?:?\s*(\d+)(?:\s*\((\d+)\))?', re.IGNORECASE)
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
                        # 尝试直接查找邀请链接后面的文本
                        after_text = ""
                        next_sibling = invite_link.next_sibling
                        while next_sibling and not after_text.strip():
                            if isinstance(next_sibling, str):
                                after_text = next_sibling
                            next_sibling = next_sibling.next_sibling if hasattr(next_sibling, 'next_sibling') else None
                        
                        logger.debug(f"站点 {site_name} 后续文本: {after_text}")
                        
                        if after_text:
                            # 处理格式: ": 1(0)" 或 ": 1" 或 "1(0)" 或 "1"
                            after_pattern = re.compile(r'(?::)?\s*(\d+)(?:\s*\((\d+)\))?')
                            after_match = after_pattern.search(after_text)
                            
                            if after_match:
                                # 获取永久邀请数量
                                if after_match.group(1):
                                    result["invite_status"]["permanent_count"] = int(after_match.group(1))
                                
                                # 如果有临时邀请数量
                                if len(after_match.groups()) > 1 and after_match.group(2):
                                    result["invite_status"]["temporary_count"] = int(after_match.group(2))
                                
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
                            invitee["profile_url"] = urljoin(soup.url if hasattr(soup, 'url') else "", href) if href else ""
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
                        # 扩展无限分享率识别，包括任何大小写的inf或inf.
                        elif ratio_text.lower() in ['inf.', 'inf', '无限', 'infinite', '∞']:
                            ratio_text = '∞'
                            
                        invitee["ratio"] = ratio_text
                        
                        # 计算分享率数值
                        try:
                            if ratio_text == '∞':
                                invitee["ratio_value"] = 1e20  # 用一个非常大的数代表无限
                            else:
                                # 正确处理千分位逗号 - 使用更好的方法完全移除千分位逗号
                                # 先将所有千分位逗号去掉，然后再处理小数点
                                normalized_ratio = ratio_text
                                # 循环处理，直到没有千分位逗号
                                while ',' in normalized_ratio:
                                    # 检查每个逗号是否是千分位分隔符
                                    comma_positions = [pos for pos, char in enumerate(normalized_ratio) if char == ',']
                                    for pos in comma_positions:
                                        # 如果逗号后面是数字，且前面也是数字，则视为千分位逗号
                                        if (pos > 0 and pos < len(normalized_ratio) - 1 and 
                                            normalized_ratio[pos-1].isdigit() and normalized_ratio[pos+1].isdigit()):
                                            normalized_ratio = normalized_ratio[:pos] + normalized_ratio[pos+1:]
                                            break
                                    else:
                                        # 如果没有找到千分位逗号，退出循环
                                        break
                                
                                # 最后，将任何剩余的逗号替换为小数点（可能是小数点表示）
                                normalized_ratio = normalized_ratio.replace(',', '.')
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
                    
                    # 做种时魔/当前纯做种时魔 - 这是我们需要特别解析的字段
                    elif any(keyword in header for keyword in ['做种时魔', '纯做种时魔', '当前纯做种时魔', '做种积分', 'seed bonus', 'seed magic', 
                                                              '单种魔力', '单种杏仁', '单种UCoin', '单种麦粒', '单种银元', '单种电力值','单种松子','单种松子值', '单种憨豆', 
                                                              '单种茉莉', '单种蟹币值', '单种鲸币', '单种蝌蚪', '单种灵石', '单种爆米花', '单种冰晶', 
                                                              '单种积分', '单种魅力值', '单种猫粮', '单种星焱']):
                        invitee["seed_magic"] = cell_text
                    
                    # 后宫加成 - 新增字段
                    elif any(keyword in header for keyword in ['后宫加成', '後宮加成', 'invitee bonus', 'bonus']):
                        # 统一字段名为seed_bonus，与butterfly处理器保持一致
                        invitee["seed_bonus"] = cell_text
                    
                    # 最后做种汇报时间/最后做种报告 - 新增字段
                    elif any(keyword in header for keyword in ['最后做种汇报', '最后做种报告', '最后做种', '最後做種報告', 'last seed report']):
                        invitee["last_seed_report"] = cell_text
                    
                    # 做种魔力/积分/加成
                    elif any(keyword in header for keyword in ['魔力', 'magic', '积分', 'bonus', '加成', 'leeched', '杏仁', 'ucoin', '麦粒', '银元',
                                                              '电力值', '憨豆', '茉莉', '蟹币值', '鲸币', '蝌蚪', '灵石', '爆米花', '冰晶', '魅力值', 
                                                              '猫粮', '星焱','松子','松子值']):
                        header_lower = header.lower()
                        # 所有魔力值类型名称都统一存储到magic字段
                        if any(keyword in header_lower for keyword in ['魔力', 'magic', '杏仁', 'ucoin', '麦粒', '银元', '电力值', '憨豆', 
                                                                      '茉莉', '蟹币值', '蟹币值', '鲸币', '蝌蚪', '灵石', '爆米花', '冰晶', '魅力值', 
                                                                      '猫粮', '星焱','松子','松子值']):
                            invitee["magic"] = cell_text
                        elif '加成' in header_lower or 'bonus' in header_lower:
                            invitee["bonus"] = cell_text
                        elif '积分' in header_lower or 'credit' in header_lower:
                            invitee["credit"] = cell_text
                        elif 'leeched' in header_lower:
                            invitee["leeched"] = cell_text
                    
                    # 其他字段处理...
                
                # 如果尚未设置enabled状态，根据行类或图标判断
                if "enabled" not in invitee:
                    invitee["enabled"] = "No" if is_banned else "Yes"
                
                # 设置状态字段(如果尚未设置)
                if "status" not in invitee:
                    invitee["status"] = "已禁用" if is_banned else "已确认"
                
                # 检查是否为无数据用户（上传和下载都为0）
                is_no_data = False
                if "uploaded" in invitee and "downloaded" in invitee:
                    # 字符串判断
                    if isinstance(invitee["uploaded"], str) and isinstance(invitee["downloaded"], str):
                        is_no_data = (invitee["uploaded"] == '0' or invitee["uploaded"] == '0.00 KB' or 
                                    invitee["uploaded"].lower() == '0b') and \
                                    (invitee["downloaded"] == '0' or invitee["downloaded"] == '0.00 KB' or 
                                    invitee["downloaded"].lower() == '0b')
                    # 数值判断
                    elif isinstance(invitee["uploaded"], (int, float)) and isinstance(invitee["downloaded"], (int, float)):
                        is_no_data = invitee["uploaded"] == 0 and invitee["downloaded"] == 0

                # 添加数据状态标记
                if is_no_data:
                    invitee["data_status"] = "无数据"
                
                # 计算分享率健康状态
                if "ratio_value" in invitee:
                    if is_no_data:
                        invitee["ratio_health"] = "neutral"
                        invitee["ratio_label"] = ["无数据", "grey"]
                    elif invitee["ratio_value"] >= 1e20:
                        invitee["ratio_health"] = "excellent"
                    elif invitee["ratio_value"] >= 1.0:
                        invitee["ratio_health"] = "good"
                    elif invitee["ratio_value"] >= 0.5:
                        invitee["ratio_health"] = "warning"
                    else:
                        invitee["ratio_health"] = "danger"
                else:
                    # 处理没有ratio_value的情况
                    if is_no_data:
                        invitee["ratio_health"] = "neutral" 
                        invitee["ratio_label"] = ["无数据", "grey"]
                    elif "ratio" in invitee and invitee["ratio"] == "∞":
                        invitee["ratio_health"] = "excellent"
                    else:
                        invitee["ratio_health"] = "unknown"
                
                # 设置分享率标签
                if "ratio_label" not in invitee:
                    if "ratio_health" in invitee:
                        if invitee["ratio_health"] == "excellent":
                            invitee["ratio_label"] = ["无限", "green"]
                        elif invitee["ratio_health"] == "good":
                            invitee["ratio_label"] = ["良好", "green"]
                        elif invitee["ratio_health"] == "warning":
                            invitee["ratio_label"] = ["较低", "orange"]
                        elif invitee["ratio_health"] == "danger":
                            invitee["ratio_label"] = ["危险", "red"]
                        elif invitee["ratio_health"] == "neutral":
                            invitee["ratio_label"] = ["无数据", "grey"]
                        else:
                            invitee["ratio_label"] = ["未知", "grey"]
                
                # 将解析到的用户添加到列表中
                if invitee.get("username"):
                    result["invitees"].append(invitee)
            
            # 如果已找到用户数据，跳出循环
            if result["invitees"]:
                if is_next_page:
                    logger.info(f"站点 {site_name} 从翻页中解析到 {len(result['invitees'])} 个后宫成员")
                else:
                    logger.info(f"站点 {site_name} 从首页解析到 {len(result['invitees'])} 个后宫成员")
                break
        
        return result

    def _parse_bonus_shop(self, site_name: str, html_content: str) -> Dict[str, Any]:
        """
        解析魔力值商店页面
        :param site_name: 站点名称
        :param html_content: HTML内容
        :return: 魔力值和邀请价格信息
        """
        result = {
            "bonus": 0,                  # 用户当前魔力值
            "permanent_invite_price": 0, # 永久邀请价格
            "temporary_invite_price": 0  # 临时邀请价格
        }
        
        try:
            # 初始化BeautifulSoup对象
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # 1. 查找当前魔力值
            # 先尝试从特定HTML元素中提取魔力值
            bonus_found = False
            
            # 尝试从常见的显示位置提取魔力值
            bonus_elements = [
                # 类似于"用你的魔力值（当前141,725.2）换东东！"的文本
                soup.select_one('td.text[align="center"]'),
                # 表格中包含魔力值的单元格
                soup.select_one('table td:contains("魔力值"), table td:contains("工分"), table td:contains("积分"), ' + 
                              'table td:contains("杏仁值"), table td:contains("UCoin"), table td:contains("麦粒"), ' + 
                              'table td:contains("银元"), table td:contains("电力值"), table td:contains("憨豆"), ' + 
                              'table td:contains("茉莉"), table td:contains("蟹币值"), table td:contains("蟹币值"), table td:contains("鲸币"), ' + 
                              'table td:contains("蝌蚪"), table td:contains("灵石"), table td:contains("爆米花"), ' + 
                              'table td:contains("冰晶"), table td:contains("魅力值"), table td:contains("猫粮"), ' + 
                              'table td:contains("星焱"), table td:contains("音浪"), table td:contains("金元宝"), table td:contains("松子"), table td:contains("松子值")'),
                # 页面顶部通常显示用户信息的区域
                soup.select_one('#info_block, .info, #userinfo')
            ]
            
            for element in bonus_elements:
                if element:
                    element_text = element.get_text()
                    bonus_patterns = [
                        # 标准魔力值格式
                        r'魔力值[^(]*\(当前([\d,\.]+)[^)]*\)',
                        r'工分[^(]*\(当前([\d,\.]+)[^)]*\)',
                        r'用你的魔力值[^(]*\(当前([\d,\.]+)[^)]*\)',
                        r'用你的工分[^(]*\(当前([\d,\.]+)[^)]*\)',
                        r'当前([\d,\.]+)[^)]*魔力',
                        r'当前([\d,\.]+)[^)]*工分',
                        
                        # 特殊站点魔力值格式
                        r'杏仁值[^(]*\(当前([\d,\.]+)[^)]*\)',
                        r'UCoin[^(]*\(当前([\d,\.]+)[^)]*\)',
                        r'麦粒[^(]*\(当前([\d,\.]+)[^)]*\)',
                        r'银元[^(]*\(当前([\d,\.]+)[^)]*\)',
                        r'电力值[^(]*\(当前([\d,\.]+)[^)]*\)',
                        r'松子[^(]*\(当前([\d,\.]+)[^)]*\)',
                        r'松子值[^(]*\(当前([\d,\.]+)[^)]*\)',
                        r'憨豆[^(]*\(当前([\d,\.]+)[^)]*\)',
                        r'茉莉[^(]*\(当前([\d,\.]+)[^)]*\)',
                        r'蟹币值*[^(]*\(当前([\d,\.]+)[^)]*\)',  # 修改：同时支持蟹币和蟹币值
                        r'鲸币[^(]*\(当前([\d,\.]+)[^)]*\)',
                        r'蝌蚪[^(]*\(当前([\d,\.]+)[^)]*\)',
                        r'灵石[^(]*\(当前([\d,\.]+)[^)]*\)',
                        r'爆米花[^(]*\(当前([\d,\.]+)[^)]*\)',
                        r'冰晶[^(]*\(当前([\d,\.]+)[^)]*\)',
                        r'积分[^(]*\(当前([\d,\.]+)[^)]*\)',
                        r'魅力值[^(]*\(当前([\d,\.]+)[^)]*\)',
                        r'猫粮[^(]*\(当前([\d,\.]+)[^)]*\)',
                        r'星焱[^(]*\(当前([\d,\.]+)[^)]*\)',
                        r'音浪[^(]*\(当前([\d,\.]+)[^)]*\)',
                        r'金元宝[^(]*\(当前([\d,\.]+)[^)]*\)',
                        
                        r'当前([\d,\.]+)[^)]*杏仁值',
                        r'当前([\d,\.]+)[^)]*UCoin',
                        r'当前([\d,\.]+)[^)]*麦粒',
                        r'当前([\d,\.]+)[^)]*银元',
                        r'当前([\d,\.]+)[^)]*电力值',
                        r'当前([\d,\.]+)[^)]*松子',
                        r'当前([\d,\.]+)[^)]*松子值',
                        r'当前([\d,\.]+)[^)]*憨豆',
                        r'当前([\d,\.]+)[^)]*茉莉',
                        r'当前([\d,\.]+)[^)]*蟹币值',
                        r'当前([\d,\.]+)[^)]*鲸币',
                        r'当前([\d,\.]+)[^)]*蝌蚪',
                        r'当前([\d,\.]+)[^)]*灵石',
                        r'当前([\d,\.]+)[^)]*爆米花',
                        r'当前([\d,\.]+)[^)]*冰晶',
                        r'当前([\d,\.]+)[^)]*积分',
                        r'当前([\d,\.]+)[^)]*魅力值',
                        r'当前([\d,\.]+)[^)]*猫粮',
                        r'当前([\d,\.]+)[^)]*星焱',
                        r'当前([\d,\.]+)[^)]*音浪',
                        r'当前([\d,\.]+)[^)]*金元宝',
                        
                        r'当前([\d,\.]+)[^)]*杏仁值',
                        r'当前([\d,\.]+)[^)]*UCoin',
                        r'当前([\d,\.]+)[^)]*麦粒', 
                        r'当前([\d,\.]+)[^)]*银元',
                        r'当前([\d,\.]+)[^)]*电力值',
                        r'当前([\d,\.]+)[^)]*松子',
                        r'当前([\d,\.]+)[^)]*松子值',
                        r'当前([\d,\.]+)[^)]*憨豆',
                        r'当前([\d,\.]+)[^)]*茉莉',
                        r'当前([\d,\.]+)[^)]*蟹币值',
                        r'当前([\d,\.]+)[^)]*鲸币',
                        r'当前([\d,\.]+)[^)]*蝌蚪',
                        r'当前([\d,\.]+)[^)]*灵石',
                        r'当前([\d,\.]+)[^)]*爆米花',
                        r'当前([\d,\.]+)[^)]*冰晶',
                        r'当前([\d,\.]+)[^)]*魅力值',
                        r'当前([\d,\.]+)[^)]*猫粮',
                        r'当前([\d,\.]+)[^)]*星焱',
                        r'当前([\d,\.]+)[^)]*音浪',
                        r'当前([\d,\.]+)[^)]*金元宝',
                        
                        r'([\d,\.]+)\s*个杏仁值',
                        r'([\d,\.]+)\s*个UCoin',
                        r'([\d,\.]+)\s*个麦粒',
                        r'([\d,\.]+)\s*个银元',
                        r'([\d,\.]+)\s*个电力值',
                        r'([\d,\.]+)\s*个松子',
                        r'([\d,\.]+)\s*个松子值',
                        r'([\d,\.]+)\s*个憨豆',
                        r'([\d,\.]+)\s*个茉莉',
                        r'([\d,\.]+)\s*个蟹币值',
                        r'([\d,\.]+)\s*个鲸币',
                        r'([\d,\.]+)\s*个蝌蚪',
                        r'([\d,\.]+)\s*个灵石',
                        r'([\d,\.]+)\s*个爆米花',
                        r'([\d,\.]+)\s*个冰晶',
                        r'([\d,\.]+)\s*个魅力值',
                        r'([\d,\.]+)\s*个猫粮',
                        r'([\d,\.]+)\s*个星焱',
                        r'([\d,\.]+)\s*个音浪',
                        r'([\d,\.]+)\s*个金元宝'
                    ]
                    
                    for pattern in bonus_patterns:
                        bonus_match = re.search(pattern, element_text, re.IGNORECASE)
                        if bonus_match:
                            bonus_str = bonus_match.group(1).replace(',', '')
                            try:
                                result["bonus"] = float(bonus_str)
                                logger.info(f"站点 {site_name} 从元素中提取到魔力值/特殊积分: {result['bonus']}")
                                
                                # 检查魔力值是否可能是时魔信息
                                if result["bonus"] < 100 and '时魔' in element_text or '每小时' in element_text:
                                    logger.warning(f"站点 {site_name} 提取的可能是时魔信息而非魔力值: {result['bonus']}")
                                    result["bonus"] = 0
                                    bonus_found = False
                                    continue
                                
                                bonus_found = True
                                break
                            except ValueError:
                                continue
                
                if bonus_found:
                    break
            
            # 如果从元素中没找到魔力值，则从整个页面文本中提取
            if not bonus_found:
                # 查找包含魔力值的文本，添加更多可能的格式匹配模式
                bonus_patterns = [
                    # 常规魔力值格式
                    r'魔力值\s*[:：]\s*([\d,\.]+)',
                    r'当前魔力值[^(]*\(当前([\d,\.]+)\)',
                    r'当前([\d,\.]+)[^)]*魔力值',
                    r'魔力值[^(]*\(当前([\d,\.]+)\)',
                    r'用你的魔力值[^(]*\(当前([\d,\.]+)[^)]*\)',
                    
                    # 工分格式
                    r'工分\s*[:：]\s*([\d,\.]+)',
                    r'当前工分[^(]*\(当前([\d,\.]+)\)',
                    r'当前([\d,\.]+)[^)]*工分',
                    r'工分[^(]*\(当前([\d,\.]+)\)',
                    r'用你的工分[^(]*\(当前([\d,\.]+)[^)]*\)',
                    
                    # 积分/欢乐值等其他变体
                    r'积分\s*[:：]\s*([\d,\.]+)',
                    r'欢乐值\s*[:：]\s*([\d,\.]+)',
                    r'當前\s*[:：]?\s*([\d,\.]+)',
                    r'目前\s*[:：]?\s*([\d,\.]+)',
                    r'bonus\s*[:：]?\s*([\d,\.]+)',
                    r'([\d,\.]+)\s*个魔力值',
                    r'([\d,\.]+)\s*个工分',
                    
                    # 特殊站点魔力值格式
                    r'杏仁值\s*[:：]\s*([\d,\.]+)',
                    r'UCoin\s*[:：]\s*([\d,\.]+)',
                    r'麦粒\s*[:：]\s*([\d,\.]+)',
                    r'银元\s*[:：]\s*([\d,\.]+)',
                    r'电力值\s*[:：]\s*([\d,\.]+)',
                    r'松子\s*[:：]\s*([\d,\.]+)',
                    r'松子值\s*[:：]\s*([\d,\.]+)',
                    r'憨豆\s*[:：]\s*([\d,\.]+)',
                    r'茉莉\s*[:：]\s*([\d,\.]+)',
                    r'蟹币值*\s*[:：]\s*([\d,\.]+)',  # 修改：同时支持蟹币和蟹币值
                    r'鲸币\s*[:：]\s*([\d,\.]+)',
                    r'蝌蚪\s*[:：]\s*([\d,\.]+)',
                    r'灵石\s*[:：]\s*([\d,\.]+)',
                    r'爆米花\s*[:：]\s*([\d,\.]+)',
                    r'冰晶\s*[:：]\s*([\d,\.]+)',
                    r'魅力值\s*[:：]\s*([\d,\.]+)',
                    r'猫粮\s*[:：]\s*([\d,\.]+)',
                    r'星焱\s*[:：]\s*([\d,\.]+)',
                    
                    r'当前杏仁值[^(]*\(当前([\d,\.]+)\)',
                    r'当前UCoin[^(]*\(当前([\d,\.]+)\)',
                    r'当前麦粒[^(]*\(当前([\d,\.]+)\)',
                    r'当前银元[^(]*\(当前([\d,\.]+)\)',
                    r'当前电力值[^(]*\(当前([\d,\.]+)\)',
                    r'当前松子[^(]*\(当前([\d,\.]+)\)',
                    r'当前松子值[^(]*\(当前([\d,\.]+)\)',
                    r'当前憨豆[^(]*\(当前([\d,\.]+)\)',
                    r'当前茉莉[^(]*\(当前([\d,\.]+)\)',
                    r'当前蟹币值[^(]*\(当前([\d,\.]+)\)',
                    r'当前鲸币[^(]*\(当前([\d,\.]+)\)',
                    r'当前蝌蚪[^(]*\(当前([\d,\.]+)\)',
                    r'当前灵石[^(]*\(当前([\d,\.]+)\)',
                    r'当前爆米花[^(]*\(当前([\d,\.]+)\)',
                    r'当前冰晶[^(]*\(当前([\d,\.]+)\)',
                    r'当前魅力值[^(]*\(当前([\d,\.]+)\)',
                    r'当前猫粮[^(]*\(当前([\d,\.]+)\)',
                    r'当前星焱[^(]*\(当前([\d,\.]+)\)',
                    
                    r'当前([\d,\.]+)[^)]*杏仁值',
                    r'当前([\d,\.]+)[^)]*UCoin',
                    r'当前([\d,\.]+)[^)]*麦粒', 
                    r'当前([\d,\.]+)[^)]*银元',
                    r'当前([\d,\.]+)[^)]*电力值',
                    r'当前([\d,\.]+)[^)]*电力值',
                    r'当前([\d,\.]+)[^)]*电力值',
                    r'当前([\d,\.]+)[^)]*憨豆',
                    r'当前([\d,\.]+)[^)]*茉莉',
                    r'当前([\d,\.]+)[^)]*蟹币值',
                    r'当前([\d,\.]+)[^)]*鲸币',
                    r'当前([\d,\.]+)[^)]*蝌蚪',
                    r'当前([\d,\.]+)[^)]*灵石',
                    r'当前([\d,\.]+)[^)]*爆米花',
                    r'当前([\d,\.]+)[^)]*冰晶',
                    r'当前([\d,\.]+)[^)]*魅力值',
                    r'当前([\d,\.]+)[^)]*猫粮',
                    r'当前([\d,\.]+)[^)]*星焱',
                    r'当前([\d,\.]+)[^)]*音浪',
                    r'当前([\d,\.]+)[^)]*金元宝',
                    
                    r'([\d,\.]+)\s*个杏仁值',
                    r'([\d,\.]+)\s*个UCoin',
                    r'([\d,\.]+)\s*个麦粒',
                    r'([\d,\.]+)\s*个银元',
                    r'([\d,\.]+)\s*个电力值',
                    r'([\d,\.]+)\s*个松子',
                    r'([\d,\.]+)\s*个松子值',
                    r'([\d,\.]+)\s*个憨豆',
                    r'([\d,\.]+)\s*个茉莉',
                    r'([\d,\.]+)\s*个蟹币值',
                    r'([\d,\.]+)\s*个鲸币',
                    r'([\d,\.]+)\s*个蝌蚪',
                    r'([\d,\.]+)\s*个灵石',
                    r'([\d,\.]+)\s*个爆米花',
                    r'([\d,\.]+)\s*个冰晶',
                    r'([\d,\.]+)\s*个魅力值',
                    r'([\d,\.]+)\s*个猫粮',
                    r'([\d,\.]+)\s*个星焱',
                    r'([\d,\.]+)\s*个音浪',
                    r'([\d,\.]+)\s*个金元宝'
                ]
                
                # 页面文本
                page_text = soup.get_text()
                
                # 尝试不同的正则表达式查找魔力值
                for pattern in bonus_patterns:
                    bonus_match = re.search(pattern, page_text, re.IGNORECASE)
                    if bonus_match:
                        bonus_str = bonus_match.group(1).replace(',', '')
                        try:
                            result["bonus"] = float(bonus_str)
                            logger.info(f"站点 {site_name} 从页面文本中提取到魔力值/特殊积分: {result['bonus']}")
                            
                            # 检查是否在时魔相关上下文中
                            context_text = page_text[max(0, page_text.find(bonus_str) - 50):page_text.find(bonus_str) + 50]
                            if result["bonus"] < 100 and ('时魔' in context_text or '每小时' in context_text):
                                logger.warning(f"站点 {site_name} 页面文本中提取的可能是时魔信息而非魔力值: {result['bonus']}")
                                continue
                            
                            break
                        except ValueError:
                            continue
            
            # 2. 查找邀请价格
            # 查找表格
            tables = soup.select('table')
            for table in tables:
                # 检查表头是否包含交换/价格等关键词
                headers = table.select('td.colhead, th.colhead, td, th')
                header_text = ' '.join([h.get_text().lower() for h in headers])
                
                bonus_keywords = ['魔力值', '积分', 'bonus', '工分', '杏仁值', 'ucoin', '麦粒', '银元', 
                                 '电力值','松子','松子值', '憨豆', '茉莉', '蟹币', '蟹币值', '鲸币', '蝌蚪', '灵石', '爆米花', 
                                 '冰晶', '魅力值', '猫粮', '星焱', '音浪', '金元宝']
                
                if any(keyword in header_text for keyword in bonus_keywords):
                    # 遍历表格行
                    rows = table.select('tr')
                    for row in rows:
                        cells = row.select('td')
                        if len(cells) < 3:
                            continue
                            
                        # 获取行文本
                        row_text = row.get_text().lower()
                        
                        # 检查是否包含邀请关键词 - 增加更多可能的称呼
                        invite_keywords = [
                            '邀请名额', '邀請名額', '邀请名额', 'invite', 
                            '探视权', '探視權', '查看权', '查看權', 
                            '临时邀请名额', '臨時邀請名額', '临时探视'
                        ]
                        
                        # 避免误识别 - 排除包含特定关键词的行
                        exclude_keywords = ['魔力每小时', '每小时能获取', '当前每小时', '时魔', '纯做种', '做种时魔', '做种积分', '单种魔力']
                        should_exclude = any(keyword in row_text for keyword in exclude_keywords)
                        
                        is_invite_row = any(keyword in row_text for keyword in invite_keywords) and not should_exclude
                        if is_invite_row:
                            # 判断是永久邀请还是临时邀请
                            is_temporary = '临时' in row_text or '臨時' in row_text or 'temporary' in row_text
                            
                            # 查找价格列(通常是第3列)
                            price_cell = None
                            
                            # 检查单元格数量
                            if len(cells) >= 3:
                                for i, cell in enumerate(cells):
                                    cell_text = cell.get_text().lower()
                                    price_keywords = ['价格', '售价', 'price'] + bonus_keywords
                                    if any(keyword in cell_text for keyword in price_keywords):
                                        # 找到了价格列标题，下一列可能是价格
                                        if i+1 < len(cells):
                                            price_cell = cells[i+1]
                                            break
                                    elif any(price_word in cell_text for price_word in ['price', '价格', '售价']):
                                        price_cell = cell
                                        break
                            
                            # 如果没找到明确的价格列，就默认第3列
                            if not price_cell and len(cells) >= 3:
                                price_cell = cells[2]
                            
                            # 提取价格
                            if price_cell:
                                price_text = price_cell.get_text().strip()
                                try:
                                    # 尝试提取数字
                                    price_match = re.search(r'([\d,\.]+)', price_text)
                                    if price_match:
                                        price = float(price_match.group(1).replace(',', ''))
                                        
                                        # 过滤不合理的邀请价格 - 通常邀请价格在数万到百万范围
                                        # 排除可能是时魔/做种魔力信息的小数值
                                        if price > 0:
                                            # 邀请价格通常较大，小于100的可能是时魔信息
                                            if price < 100:
                                                logger.info(f"站点 {site_name} 忽略可能的时魔信息: {price}")
                                                continue
                                                
                                            if is_temporary:
                                                result["temporary_invite_price"] = price
                                                logger.info(f"站点 {site_name} 临时邀请价格: {price}")
                                            else:
                                                result["permanent_invite_price"] = price
                                                logger.info(f"站点 {site_name} 永久邀请价格: {price}")
                                except ValueError:
                                    continue
            
            return result
            
        except Exception as e:
            logger.error(f"解析站点 {site_name} 魔力值商店失败: {str(e)}")
            return result 

    def _calculate_ratio_health(self, ratio_str, uploaded, downloaded):
        """
        计算分享率健康度
        """
        try:
            # 优先使用上传下载直接计算分享率（如果都是数值类型）
            if isinstance(uploaded, (int, float)) and isinstance(downloaded, (int, float)) and downloaded > 0:
                ratio = uploaded / downloaded
                # 使用计算结果生成适当的健康状态和标签
                return self._get_health_from_ratio_value(ratio)
                
            # 检查是否是无数据情况（上传下载都是0）
            is_no_data = False
            if isinstance(uploaded, str) and isinstance(downloaded, str):
                uploaded_zero = uploaded == '0' or uploaded == '' or uploaded == '0.0' or uploaded.lower() == '0b'
                downloaded_zero = downloaded == '0' or downloaded == '' or downloaded == '0.0' or downloaded.lower() == '0b'
                is_no_data = uploaded_zero and downloaded_zero
            elif isinstance(uploaded, (int, float)) and isinstance(downloaded, (int, float)):
                is_no_data = uploaded == 0 and downloaded == 0

            if is_no_data:
                return "neutral", ["无数据", "text-grey"]
                
            # 处理无限分享率情况 - 增强检测逻辑
            if not ratio_str:
                return "neutral", ["无效", "text-grey"]
                
            # 统一处理所有表示无限的情况，忽略大小写
            if ratio_str == '∞' or ratio_str.lower() in ['inf.', 'inf', 'infinite', '无限']:
                return "excellent", ["分享率无限", "text-success"]

            # 标准化分享率字符串 - 正确处理千分位逗号
            try:
                # 使用更好的方法完全移除千分位逗号
                normalized_ratio = ratio_str
                # 循环处理，直到没有千分位逗号
                while ',' in normalized_ratio:
                    # 检查每个逗号是否是千分位分隔符
                    comma_positions = [pos for pos, char in enumerate(normalized_ratio) if char == ',']
                    for pos in comma_positions:
                        # 如果逗号后面是数字，且前面也是数字，则视为千分位逗号
                        if (pos > 0 and pos < len(normalized_ratio) - 1 and 
                            normalized_ratio[pos-1].isdigit() and normalized_ratio[pos+1].isdigit()):
                            normalized_ratio = normalized_ratio[:pos] + normalized_ratio[pos+1:]
                            break
                    else:
                        # 如果没有找到千分位逗号，退出循环
                        break
                
                # 最后，将任何剩余的逗号替换为小数点（可能是小数点表示）
                normalized_ratio = normalized_ratio.replace(',', '.')
                ratio = float(normalized_ratio)
                return self._get_health_from_ratio_value(ratio)
            except (ValueError, TypeError) as e:
                logger.error(f"分享率转换错误: {ratio_str}, 错误: {str(e)}")
                return "neutral", ["无效", "text-grey"]

        except (ValueError, TypeError) as e:
            logger.error(f"分享率计算错误: {str(e)}")
            return "neutral", ["无效", "text-grey"]
            
    def _get_health_from_ratio_value(self, ratio):
        """
        根据分享率数值获取健康状态和标签
        """
        # 分享率健康度判断
        if ratio >= 4.0:
            return "excellent", ["极好", "text-success"]
        elif ratio >= 2.0:
            return "good", ["良好", "text-success"]
        elif ratio >= 1.0:
            return "good", ["正常", "text-success"]
        elif ratio > 0:
            return "warning" if ratio >= 0.4 else "danger", ["较低", "text-warning"] if ratio >= 0.4 else ["危险", "text-error"]
        else:
            return "neutral", ["无数据", "text-grey"]

    def _check_ratio(self, row_data, row_html):
        """
        检查分享率是否满足条件
        """
        ratio_str = row_data.get("ratio") or ""
        
        # 处理无限分享率情况
        if ratio_str == '∞' or ratio_str.lower() in ['inf.', 'inf', 'infinite', '无限']:
            return True

        try:
            # 标准化字符串 - 正确处理千分位逗号
            # 使用更好的方法完全移除千分位逗号
            normalized_ratio = ratio_str
            # 循环处理，直到没有千分位逗号
            while ',' in normalized_ratio:
                # 检查每个逗号是否是千分位分隔符
                comma_positions = [pos for pos, char in enumerate(normalized_ratio) if char == ',']
                for pos in comma_positions:
                    # 如果逗号后面是数字，且前面也是数字，则视为千分位逗号
                    if (pos > 0 and pos < len(normalized_ratio) - 1 and 
                        normalized_ratio[pos-1].isdigit() and normalized_ratio[pos+1].isdigit()):
                        normalized_ratio = normalized_ratio[:pos] + normalized_ratio[pos+1:]
                        break
                else:
                    # 如果没有找到千分位逗号，退出循环
                    break
            
            # 最后，将任何剩余的逗号替换为小数点（可能是小数点表示）
            normalized_ratio = normalized_ratio.replace(',', '.')
            
            ratio = float(normalized_ratio) if normalized_ratio else 0
            min_ratio = self.config.get("min_ratio", 0.5)
            if ratio < min_ratio:
                return False
            return True
        except (ValueError, TypeError):
            # 转换失败时也返回True，避免误判
            return True 