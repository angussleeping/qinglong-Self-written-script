# -*- coding: utf-8 -*-
"""
阿里云盘自动签到脚本（2025年12月独立版）
说明：阿里云盘现已改为「签到即自动发放奖励」，无需手动领取
cron: 10 6 * * *
new Env('阿里云盘');
"""

import os
import sys
import traceback
from io import StringIO
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

requests.packages.urllib3.disable_warnings()


# ==================== 推送函数（兼容青龙面板） ====================
def send(title, content):
    """支持 PushPlus 和 Server 酱（青龙面板标准环境变量）"""
    push_plus_token = os.getenv("PUSH_PLUS_TOKEN")
    push_key = os.getenv("PUSH_KEY")  # Server 酱 SCKEY（sct开头）
    sent = False

    # PushPlus 推送
    if push_plus_token:
        try:
            resp = requests.post(
                "http://www.pushplus.plus/send",
                json={
                    "token": push_plus_token,
                    "title": title,
                    "content": content,
                    "template": "html"
                },
                timeout=10
            )
            if resp.status_code == 200 and resp.json().get("code") == 200:
                print("✅ PushPlus 推送成功")
                sent = True
            else:
                print("❌ PushPlus 推送失败")
        except Exception as e:
            print(f"❌ PushPlus 异常: {e}")

    # Server 酱推送（SCT）
    if not sent and push_key:
        try:
            resp = requests.post(
                f"https://sctapi.ftqq.com/{push_key}.send",
                data={"title": title, "desp": content},
                timeout=10
            )
            if resp.status_code == 200 and resp.json().get("errno") == 0:
                print("✅ Server 酱推送成功")
                sent = True
            else:
                print("❌ Server 酱推送失败")
        except Exception as e:
            print(f"❌ Server 酱异常: {e}")

    if not sent:
        print("⚠️ 未配置有效推送方式（请设置 PUSH_KEY 或 PUSH_PLUS_TOKEN）")


# ==================== 阿里云盘签到类 ====================
class ALiYun:
    def __init__(self, tokens):
        self.sio = StringIO()
        self.tokens = tokens
        self.session = requests.Session()
        retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
        self.session.mount("https://", HTTPAdapter(max_retries=retries))

    def _get_access_token(self, refresh_token):
        """通过 refresh_token 获取 access_token 和昵称"""
        url = 'https://auth.aliyundrive.com/v2/account/token'
        data = {'grant_type': 'refresh_token', 'refresh_token': refresh_token}
        try:
            res = self.session.post(url, json=data, timeout=10).json()
            if 'code' in res and res['code'] in ['RefreshTokenExpired', 'InvalidParameter.RefreshToken']:
                return False, "", "", res.get('message', 'Token 无效或已过期')
            nick_name = res.get('nick_name') or res.get('user_name', '未知用户')
            access_token = res['access_token']
            return True, nick_name, access_token, '成功'
        except Exception as e:
            return False, "", "", f"请求异常: {str(e)}"

    def _check_in(self, access_token):
        """签到（模拟移动端，含 x-canary）"""
        url = 'https://member.aliyundrive.com/v1/activity/sign_in_list'
        payload = {'isReward': False}
        params = {'_rx-s': 'mobile'}
        headers = {
            'Authorization': f'Bearer {access_token}',
            'User-Agent': 'Mozilla/5.0 (Linux; Android 12; Pixel 6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Mobile Safari/537.36 acf/1.0.0',
            'x-canary': 'client=Android,app=adrive,version=v3.45.1'
        }
        try:
            response = self.session.post(url, json=payload, params=params, headers=headers, timeout=10)
            data = response.json()
            if not data.get('success'):
                return False, -1, data.get('message', '未知错误')
            signin_count = data['result']['signInCount']
            return True, signin_count, '签到成功'
        except Exception as e:
            return False, -1, f"签到异常: {str(e)}"

    def run(self):
        print("【阿里云盘 日志】")
        self.sio.write("【阿里云盘 执行结果】\n\n")
        all_success = True

        for i, refresh_token in enumerate(self.tokens):
            name = f"账号{i+1}"
            refresh_token = refresh_token.strip()
            if not refresh_token:
                self.sio.write(f"👤 账号：{name}\n状态: ❌ 缺少 refresh_token\n\n")
                all_success = False
                continue

            try:
                flag, user_name, access_token, msg = self._get_access_token(refresh_token)
                if not flag:
                    self.sio.write(f"👤 账号：{name}\n状态: ❌ Token 失效 → {msg}\n\n")
                    all_success = False
                    continue

                flag, signin_count, msg = self._check_in(access_token)
                if not flag:
                    self.sio.write(f"👤 账号：{user_name}\n签到: ❌ {msg}\n\n")
                    all_success = False
                    continue

                # ✅ 2025年起：签到成功即自动发放奖励，无需领奖接口
                self.sio.write(
                    f"👤 账号：{user_name}\n"
                    f"签到: ✅ 本月第 {signin_count} 次\n"
                    f"奖励: ✅ 系统已自动发放 1T 容量 1天\n\n"
                )

            except Exception as e:
                error_info = f"执行异常: {str(e)}"
                print(f"{name}: {error_info}\n{traceback.format_exc()}")
                self.sio.write(f"👤 账号：{name}\n状态: ❌ {error_info}\n\n")
                all_success = False

        output = self.sio.getvalue()
        print("\n" + output)
        send("✅ 阿里云盘签到成功" if all_success else "❌ 阿里云盘签到失败", output.replace("\n", "<br>"))


# ==================== 从环境变量加载配置 ====================
def load_config():
    env_tokens = os.getenv("ALIYUN_TOKEN", "")
    if not env_tokens:
        print("❌ 请在青龙面板配置环境变量 ALIYUN_TOKEN（多个用 & 分隔）")
        sys.exit(1)

    tokens = [t.strip() for t in env_tokens.split("&") if t.strip()]
    if not tokens:
        print("❌ ALIYUN_TOKEN 格式错误，未检测到有效 token")
        sys.exit(1)

    return tokens


# ==================== 主程序入口 ====================
if __name__ == '__main__':
    tokens = load_config()
    aly = ALiYun(tokens)
    aly.run()