# -*- coding: utf-8 -*-
"""
AcFun 自动任务脚本（青龙面板 · 单账号 · 修复用户名/弹幕/投蕉问题）
cron: 0 6 * * *
new Env('AcFun');
"""

import sys
import io
import json
# 设置标准输出为UTF-8编码
sys.stdout = io.TextIOWrapper(sys.stdout.detach(), encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.detach(), encoding='utf-8')

import os
import re
import time
import traceback
import urllib.parse
from io import StringIO
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

requests.packages.urllib3.disable_warnings()


# ==================== 推送函数 ====================
def send(title, content):
    sent_any = False

    push_plus_token = os.getenv("PUSH_PLUS_TOKEN")
    if push_plus_token:
        try:
            resp = requests.post(
                "http://www.pushplus.plus/send",
                json={"token": push_plus_token, "title": title, "content": content, "template": "html"},
                timeout=10
            )
            if resp.status_code == 200 and resp.json().get("code") == 200:
                print("✅ PushPlus 推送成功")
                sent_any = True
            else:
                print(f"❌ PushPlus 失败: {resp.text}")
        except Exception as e:
            print(f"❌ PushPlus 异常: {e}")

    push_key = os.getenv("PUSH_KEY")
    if push_key:
        try:
            resp = requests.post(
                f"https://sctapi.ftqq.com/{push_key}.send",
                data={"title": title, "desp": content},
                timeout=10
            )
            if resp.status_code == 200 and resp.json().get("errno") == 0:
                print("✅ Server 酱推送成功")
                sent_any = True
            else:
                print(f"❌ Server 酱失败: {resp.text}")
        except Exception as e:
            print(f"❌ Server 酱异常: {e}")

    if not sent_any:
        print("⚠️ 未配置推送密钥，跳过通知")


# ==================== 工具函数 ====================
def clean_cookie_string(cookie_str):
    """清理 Cookie 中的零宽字符和控制字符"""
    if not cookie_str:
        return ""
    cleaned = re.sub(r'[\u200b-\u200f\u202a-\u202e\ufeff]', '', cookie_str)
    cleaned = ''.join(ch for ch in cleaned if ord(ch) >= 32 or ch in '\t\n\r')
    return cleaned.strip()


def parse_cookie_str(cookie_str):
    """解析 Cookie 字符串为字典（后出现的值覆盖前面的）"""
    cookies = {}
    for item in cookie_str.split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            cookies[k] = v
    return cookies


def extract_auth_key(cookie_str):
    match = re.search(r'auth_key=([^;\s]+)', cookie_str)
    return match.group(1) if match else None


# ==================== AcFun 任务类 ====================
class AcFun:
    def __init__(self, raw_cookie_str):
        self.raw_cookie_str = raw_cookie_str
        self.cookie_str = clean_cookie_string(raw_cookie_str)
        if not self.cookie_str:
            raise ValueError("清理后 Cookie 为空")

        self.cookies_dict = parse_cookie_str(self.cookie_str)
        self.auth_key = extract_auth_key(self.cookie_str)

        # ✅ 优先从 Cookie 中获取用户名（支持中文）
        self.username = "未知用户"
        if "ac_username" in self.cookies_dict:
            encoded_uname = self.cookies_dict["ac_username"]
            try:
                self.username = urllib.parse.unquote(encoded_uname)
            except:
                self.username = encoded_uname
        else:
            # 备用：尝试通过 API 获取（但可能因会话不全失败）
            self._get_username_from_api()

        self.sio = StringIO()
        self.session = requests.Session()
        retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
        self.session.mount("https://", HTTPAdapter(max_retries=retries))
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        })

    def _get_username_from_api(self):
        """备用：通过 API 获取用户名"""
        url = "https://www.acfun.cn/rest/pc-direct/user/personalInfo"
        try:
            resp = self.session.get(url, cookies=self.cookies_dict, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("result") == 0:
                    self.username = str(data.get("username", "未知用户")).strip()
        except:
            pass

    def get_top_video(self):
        url = "https://www.acfun.cn/rest/pc-direct/rank/channel"
        data = {"channelId": "0", "rankPeriod": "DAY"}
        for _ in range(3):  # 增加重试机制
            try:
                resp = self.session.post(url, data=data, timeout=10)
                if resp.status_code == 200:
                    rank_list = resp.json().get("rankList", [])
                    if rank_list:
                        return str(rank_list[0]["contentId"])
            except Exception as e:
                print(f"获取视频ID失败: {str(e)}")
                time.sleep(1)
        return "27259341"  # 仍然使用默认值作为最后的 fallback

    def sign_in(self):
        url = "https://www.acfun.cn/rest/pc-direct/user/signIn"
        try:
            resp = self.session.post(url, cookies=self.cookies_dict, timeout=10)
            resp.raise_for_status()  # 检查HTTP状态码
            result = resp.json()
            if result.get("result") == 0:
                return "签到成功"
            else:
                return f"签到失败: {result.get('msg', '未知错误')}"
        except requests.exceptions.RequestException as e:
            return f"签到网络异常: {str(e)}"
        except ValueError as e:
            return f"签到解析异常: {str(e)}"
        except Exception as e:
            return f"签到其他异常: {str(e)}"

    def get_st_token(self):
        url = "https://id.app.acfun.cn/rest/web/token/get"
        data = {"sid": "acfun.midground.api"}
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        try:
            resp = self.session.post(url, cookies=self.cookies_dict, data=data, headers=headers, timeout=10)
            res_json = resp.json()
            if res_json.get("result") == 0:
                return res_json.get("acfun.midground.api_st")
        except:
            pass
        return None

    def like(self, token, content_id):
        if not token:
            return "跳过点赞（token 获取失败）"
        url = "https://api.kuaishouzt.com/rest/zt/interact/add"
        cookies = {"acfun.midground.api_st": token, "kpn": "ACFUN_APP"}
        data = {
            "interactType": "1",
            "objectId": content_id,
            "objectType": "2",
            "subBiz": "mainApp"
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        try:
            resp = self.session.post(url, cookies=cookies, data=data, headers=headers, timeout=10)
            result = resp.json()
            if result.get("result") == 1:
                return "点赞成功"
            else:
                return "点赞失败"
        except Exception as e:
            return f"点赞异常: {str(e)}"

    def throw_banana(self, content_id):
        url = "https://www.acfun.cn/rest/pc-direct/banana/throwBanana"
        data = {"resourceId": content_id, "count": "1", "resourceType": "2"}
        try:
            resp = self.session.post(url, cookies=self.cookies_dict, data=data, timeout=10)
            result = resp.json()
            if result.get("result") == 0:
                return "投蕉成功"
            else:
                return "投蕉失败"
        except Exception as e:
            return f"投蕉异常: {str(e)}"

    def send_danmu(self, content_id):
        page_url = f"https://www.acfun.cn/v/ac{content_id}"
        try:
            resp = self.session.get(page_url, cookies=self.cookies_dict, timeout=10)
            video_id_match = re.search(r'"currentVideoId":(\d+)', resp.text)
            sub_match = re.search(r'subChannelId:(\d+),subChannelName:"([^"]+)"', resp.text)
            if not video_id_match or not sub_match:
                return "弹幕失败（页面解析失败）"
            video_id = video_id_match.group(1)
            sub_id, sub_name = sub_match.groups()
            danmu_url = "https://www.acfun.cn/rest/pc-direct/new-danmaku/add"
            danmu_data = {
                "mode": "1",
                "color": "16777215",
                "size": "25",
                "body": "打卡成功！",
                "videoId": video_id,
                "position": "5000",
                "type": "douga",
                "id": content_id,
                "subChannelId": sub_id,
                "subChannelName": sub_name
            }
            res = self.session.post(danmu_url, cookies=self.cookies_dict, data=danmu_data, timeout=10)
            result = res.json()
            if result.get("result") == 0:
                return "弹幕成功"
            else:
                return "弹幕失败"
        except Exception as e:
            return f"弹幕异常: {str(e)[:50]}"

    def share(self):
        url = "https://api-ipv6.app.acfun.cn/rest/app/task/reportTaskAction"
        params = {
            "taskType": "1",
            "market": "tencent",
            "product": "ACFUN_APP",
            "app_version": "6.42.0.1119"
        }
        headers = {"user-agent": "okhttp/3.12.1"}
        try:
            resp = self.session.get(url, cookies=self.cookies_dict, params=params, headers=headers, timeout=10)
            result = resp.json()
            if result.get("result") == 0:
                return "分享成功"
            else:
                return "分享失败"
        except:
            return "分享失败（接口可能失效）"

    def run(self):
        if not self.auth_key:
            msg = "❌ Cookie 中未找到 auth_key，请检查 ACFUN_COOKIE 环境变量"
            print(msg)
            send("AcFun 签到失败", msg)
            return

        print(f"【正在执行 AcFun 任务 - 用户名：{self.username}】")
        self.sio.write(f"用户名：{self.username}\n\n")

        try:
            import random
            content_id = self.get_top_video()
            time.sleep(random.uniform(0.5, 1.5))  # 随机延迟
    
            sign_msg = self.sign_in()
            time.sleep(random.uniform(0.5, 1.5))
    
            token = self.get_st_token()
            like_msg = self.like(token, content_id)
            time.sleep(random.uniform(0.5, 1.5))    
    
            danmu_msg = self.send_danmu(content_id)
            time.sleep(random.uniform(0.5, 1.5))
    
            banana_msg = self.throw_banana(content_id)
            time.sleep(random.uniform(0.5, 1.5))
    
            share_msg = self.share()
            time.sleep(random.uniform(0.5, 1.5))

            tasks = [sign_msg, like_msg, danmu_msg, banana_msg, share_msg]
            all_success = not any("失败" in t or "异常" in t for t in tasks)

            msg = (
                f"签到: {sign_msg}\n"
                f"点赞: {like_msg}\n"
                f"弹幕: {danmu_msg}\n"
                f"投蕉: {banana_msg}\n"
                f"分享: {share_msg}\n"
            )
            self.sio.write(msg)
            output = self.sio.getvalue()
            print("\n" + output)

            title = f"{'✅' if all_success else '❌'} AcFun - {self.username}"
            send(title, output.replace("\n", "<br>"))

        except Exception as e:
            error_detail = f"用户名：{self.username}\n执行异常:\n{traceback.format_exc()}"
            print(error_detail)
            self.sio.write("状态: ❌ 执行异常\n")
            send(f"AcFun 执行异常 - {self.username}", error_detail.replace("\n", "<br>"))


# ==================== 主程序入口 ====================
def load_cookies():
    """加载Cookie配置"""
    # 支持从环境变量或配置文件加载
    cookies = []
    # 从环境变量加载
    env_cookie = os.getenv("ACFUN_COOKIE")
    if env_cookie:
        cookies.append(env_cookie)
    # 从配置文件加载
    config_file = "acfun_config.json"
    if os.path.exists(config_file):
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                config = json.load(f)
                if "cookies" in config:
                    cookies.extend(config["cookies"])
        except Exception as e:
            print(f"加载配置文件失败: {str(e)}")
    return cookies

if __name__ == "__main__":
    cookies = load_cookies()
    if not cookies:
        msg = "❌ 未设置环境变量 ACFUN_COOKIE 或配置文件"
        print(msg)
        send("AcFun 签到失败", msg)
    else:
        for i, cookie in enumerate(cookies):
            print(f"\n【账号 {i+1}】")
            try:
                acfun = AcFun(cookie)
                acfun.run()
            except Exception as e:
                msg = f"❌ 脚本初始化失败: {str(e)}"
                print(msg)
                send(f"AcFun 初始化失败 - 账号 {i+1}", msg)
            time.sleep(2)  # 账号之间的延迟
