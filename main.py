import time
import os
import json
import re
import requests
import platform
from datetime import datetime

if "DISPLAY" not in os.environ:
    if platform.system().lower() == "linux":
        try:
            from pyvirtualdisplay import Display
            display = Display(visible=False, size=(1920, 1080))
            display.start()
            os.environ["DISPLAY"] = display.new_display_var
        except:
            pass

from seleniumbase import SB

# ================= 配置区域 =================
PROXY = os.getenv("PROXY") or None
TG_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")
BYTENUT_TOKEN = os.getenv("BYTENUT_TOKEN", "")  # 主 Token（yl-token）

# Token 缓存文件路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COOKIE_FILE = os.path.join(BASE_DIR, "artifacts", "token_cache.json")
STORAGE_KEY = "yl-token"

URL_LOGIN_PANEL = "https://www.bytenut.com/auth/login"
URL_HOMEPAGE = "https://www.bytenut.com/homepage"
API_SERVER_LIST = "https://www.bytenut.com/game-panel/api/gpPanelServer/user/servers"
API_EXTENSION_INFO = "https://www.bytenut.com/game-panel/api/gp-free-server/extension-info/{}"
API_START_SERVER = "https://www.bytenut.com/game-panel/api/serverStartQueue/requestStart/{}"

RENEW_MENU = '//li[contains(., "RENEW SERVER")]'
EXTEND_BTN = "button.extend-btn"


def parse_accounts(raw: str):
    """兼容旧格式：如果传入的是 token，直接返回单账号"""
    raw = raw.strip()
    if not raw:
        return []
    # 如果包含 ----- 则是旧格式账号密码
    if '-----' in raw:
        accounts = []
        for line in raw.split('\n'):
            line = line.strip()
            if not line or '-----' not in line:
                continue
            parts = line.split('-----', 1)
            if len(parts) == 2:
                accounts.append((parts[0].strip(), parts[1].strip()))
        return accounts
    else:
        # 纯 token 模式
        return [("__token__", raw)]


# ========== Token 缓存管理 ==========
def load_token_from_storage():
    """从本地文件加载缓存的 token"""
    try:
        if os.path.exists(COOKIE_FILE):
            with open(COOKIE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                token = data.get(STORAGE_KEY)
                if token and len(token) > 10:
                    print(f"   📦 已加载缓存 Token: {token[:10]}...")
                    return token
    except Exception as e:
        print(f"   ⚠️ 加载缓存 Token 失败: {e}")
    return None


def save_token_to_storage(token: str):
    """保存 token 到本地文件"""
    try:
        os.makedirs(os.path.dirname(COOKIE_FILE), exist_ok=True)
        with open(COOKIE_FILE, "w", encoding="utf-8") as f:
            json.dump({STORAGE_KEY: token}, f, indent=2)
        print(f"   💾 Token 已缓存到: {COOKIE_FILE}")
        return True
    except Exception as e:
        print(f"   ❌ 保存 Token 失败: {e}")
        return False


def inject_token_to_localstorage(sb, token: str):
    """
    将 yl-token 注入到 localStorage
    """
    try:
        # 先访问域名，确保页面已加载
        sb.open(URL_LOGIN_PANEL)
        sb.wait_for_element_present("body", timeout=15)
        time.sleep(2)
        
        # 使用 JS 注入 token 到 localStorage
        script = f"""
        localStorage.setItem('{STORAGE_KEY}', '{token}');
        console.log('yl-token injected');
        """
        sb.execute_script(script)
        print(f"   ✅ 已注入 {STORAGE_KEY} 到 localStorage")
        
        # 刷新页面使登录生效
        sb.driver.refresh()
        time.sleep(3)
        return True
    except Exception as e:
        print(f"   ❌ 注入 token 失败：{str(e)[:50]}")
        return False


def check_login_status(sb):
    """
    检查当前是否已登录
    失败条件：URL 匹配登录地址 OR 页面出现 "Sign In" 文字
    """
    try:
        # 获取当前 URL 并规范化
        current_url = sb.get_current_url().strip().lower()
        login_url_target = "https://www.bytenut.com/auth/login"

        # 1. 检查 URL 是否在登录页
        if login_url_target in current_url:
            print(f"   🚫 登录校验失败：当前处于登录页面")
            return False

        # 2. 检查页面中是否能检测到 "Sign In" 文字
        if sb.is_text_visible("Sign In"):
            print("   🚫 登录校验失败：页面检测到 'Sign In' 文字")
            return False
        
        # 3. 可选：检查是否有用户相关元素（增强校验）
        # 例如检查是否有用户头像、用户名等登录后的特征元素
        
        print("   ✅ 登录状态确认：未发现登录拦截特征")
        return True

    except Exception as e:
        print(f"   ⚠️ 检查登录状态时发生异常: {str(e)[:50]}")
        return False


class BytenutRenewal:

    def __init__(self):
        self.BASE_DIR = os.path.dirname(os.path.abspath(__file__))
        self.screenshot_dir = os.path.join(self.BASE_DIR, "artifacts")
        os.makedirs(self.screenshot_dir, exist_ok=True)

    def mask_account(self, u):
        if not u: return "Unknown"
        u = u.strip()
        if u == "__token__":
            return "Token:" + (u[-8:] if len(u) > 8 else u)
        if "@" in u:
            local, domain = u.split("@", 1)
            local = local[:2] + "*" * (len(local) - 2) if len(local) > 2 else local[0] + "*"
            return f"{local}@{domain}"
        return u[:2] + "*" * (len(u) - 2) if len(u) > 2 else u[0] + "*"

    def mask_server_id(self, sid):
        if not sid: return "****"
        return "****" + sid[-4:] if len(sid) > 4 else "****"

    def log(self, msg):
        print(f"[{time.strftime('%H:%M:%S')}] [INFO] {msg}", flush=True)

    def shot(self, sb, name):
        path = os.path.join(self.screenshot_dir, name)
        sb.save_screenshot(path)
        return path

    # ========== TG 通知发送 ==========
    def send_tg(self, icon, title, account_name, server_id, state_str, expiry_str, extra="", screenshot=None):
        if not TG_TOKEN or not TG_CHAT_ID:
            return
        msg = f"{icon} {title}\n\n"
        msg += f"账号: {account_name}\n"
        msg += f"服务器: {server_id}\n"
        msg += f"状态: {state_str}\n"
        msg += f"到期时间: {expiry_str}\n"
        if extra:
            msg += f"\n{extra}\n"
        msg += "\nByteNut Auto Renew"

        try:
            if screenshot and os.path.exists(screenshot):
                url = f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto"
                with open(screenshot, "rb") as f:
                    requests.post(url, data={"chat_id": TG_CHAT_ID, "caption": msg}, files={"photo": f})
            else:
                url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
                requests.post(url, data={"chat_id": TG_CHAT_ID, "text": msg})
        except Exception as e:
            self.log(f"TG发送失败: {e}")

    # ---------- Cookie / Token 获取 ----------
    def get_full_cookies(self, sb):
        try:
            result = sb.driver.execute_cdp_cmd('Network.getCookies', {})
            cookies = result.get('cookies', [])
            return {c['name']: c['value'] for c in cookies}
        except Exception as e:
            self.log(f"CDP Cookie 失败: {e}")
            return {c['name']: c['value'] for c in sb.get_cookies()}

    def get_yl_token(self, sb):
        token = sb.execute_script(
            "return localStorage.getItem('yl-token') || sessionStorage.getItem('yl-token') || '';"
        )
        return token or None

    def call_api(self, sb, url, referer=URL_HOMEPAGE, timeout=15):
        cookies = self.get_full_cookies(sb)
        headers = {
            "User-Agent": sb.execute_script("return navigator.userAgent;"),
            "Accept": "application/json, text/plain, */*",
            "Referer": referer,
        }
        yl_token = self.get_yl_token(sb)
        if yl_token:
            headers["Yl-Token"] = yl_token

        try:
            resp = requests.get(url, headers=headers, cookies=cookies, timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                if data.get('code') == 200:
                    return data.get('data')
                else:
                    self.log(f"API 业务错误: {data.get('message')}")
            else:
                self.log(f"HTTP {resp.status_code}: {resp.text[:100]}...")
        except Exception as e:
            self.log(f"API 请求异常: {e}")
        return None

    def call_api_post(self, sb, url, referer=URL_HOMEPAGE, timeout=15):
        cookies = self.get_full_cookies(sb)
        headers = {
            "User-Agent": sb.execute_script("return navigator.userAgent;"),
            "Accept": "application/json, text/plain, */*",
            "Referer": referer,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        yl_token = self.get_yl_token(sb)
        if yl_token:
            headers["Yl-Token"] = yl_token

        try:
            resp = requests.post(url, headers=headers, cookies=cookies, timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                if data.get('code') == 200:
                    return data.get('data')
                else:
                    self.log(f"API 业务错误: {data.get('message')}")
            else:
                self.log(f"HTTP {resp.status_code}: {resp.text[:100]}...")
        except Exception as e:
            self.log(f"API 请求异常: {e}")
        return None

    def get_servers_data(self, sb):
        return self.call_api(sb, API_SERVER_LIST)

    def get_extension_data(self, sb, server_id):
        referer = f"https://www.bytenut.com/free-gamepanel/{server_id}"
        return self.call_api(sb, API_EXTENSION_INFO.format(server_id), referer=referer)

    def api_start_server(self, sb, server_id):
        referer = f"https://www.bytenut.com/free-gamepanel/{server_id}"
        result = self.call_api_post(sb, API_START_SERVER.format(server_id), referer=referer)
        if result:
            self.log("开机请求已发送")
            return True
        return False

    # ---------- 移除遮挡广告（含 Cookie 弹窗处理） ----------
    def remove_overlay_ads(self, sb):
        try:
            sb.execute_script("""
                (function() {
                    // 1. 自动点击 EZ Cookie 同意按钮
                    var acceptBtn = document.getElementById('ez-accept-all');
                    if (acceptBtn) {
                        acceptBtn.click();
                    }

                    // 2. 隐藏其他广告遮挡元素
                    var selectors = [
                        'ins.adsbygoogle', 'iframe[id^="aswift"]', 'div[id^="google_ads"]',
                        'div[class*="ad-"]:not([class*="adsterra-rewarded"]):not([class*="extend-reward-dialog"])',
                        'div[class*="ads-"]',
                        'div[id*="ad-"]:not([id*="adsterra"]):not([id*="extend-reward"])',
                        'div[id*="ads-"]',
                        '.ad-container', '.ads-wrapper', '.fixed-bottom-banner',
                        '.ezoic-floating-bottom', '.fc-ab-root'
                    ];
                    selectors.forEach(function(s) {
                        document.querySelectorAll(s).forEach(function(el) {
                            if (el.innerHTML.indexOf('turnstile') !== -1 ||
                                el.innerHTML.indexOf('cf-turnstile') !== -1 ||
                                el.innerHTML.indexOf('extend-btn') !== -1 ||
                                el.innerHTML.indexOf('adsterra-rewarded') !== -1 ||
                                el.innerHTML.indexOf('Claim Reward') !== -1 ||
                                el.innerHTML.indexOf('Watch Ad') !== -1 ||
                                el.innerHTML.indexOf('reward-option') !== -1) {
                                return;
                            }
                            el.style.display = 'none';
                            el.style.visibility = 'hidden';
                            el.style.height = '0px';
                            el.width = '0px';
                        });
                    });
                    document.body.style.overflow = 'auto';
                    document.body.style.position = 'static';
                })();
            """)
        except:
            pass

    # ---------- Turnstile 处理 ----------
    def is_turnstile_present(self, sb):
        try:
            return sb.execute_script("""
                return !!(document.querySelector('.cf-turnstile') 
                       || document.querySelector('iframe[src*="challenges.cloudflare"]')
                       || document.querySelector('input[name="cf-turnstile-response"]'));
            """)
        except:
            return False

    def wait_turnstile(self, sb, timeout=60):
        if not self.is_turnstile_present(sb):
            return True
        self.log("⏳ 等待 Turnstile 验证...")
        start = time.time()
        last_click = 0
        while time.time() - start < timeout:
            self.remove_overlay_ads(sb)
            try:
                sb.execute_script("""
                    var elem = document.querySelector('.cf-turnstile');
                    if(elem) elem.scrollIntoView({block: 'center'});
                """)
            except:
                pass
            try:
                val = sb.execute_script(
                    """return document.querySelector("input[name='cf-turnstile-response']")?.value || "";"""
                )
                if len(val) > 20:
                    self.log("✅ Turnstile 通过")
                    return True
            except:
                pass
            now = time.time()
            if now - last_click > 3:
                try:
                    sb.uc_gui_click_captcha()
                    last_click = now
                except:
                    pass
            time.sleep(1)
        self.log("⚠️ Turnstile 超时")
        return False

    # ---------- 处理扩展奖励选择弹窗 ----------
    def handle_reward_picker(self, sb):
        """如果弹出 extend-reward-dialog，点击其中的 Watch Ad 按钮"""
        try:
            if not sb.execute_script("return !!document.querySelector('.extend-reward-dialog');"):
                return True
            self.log("🛡️ 处理扩展奖励选择...")
            # 点击 Watch Ad 选项
            sb.execute_script("""
                var btn = document.querySelector('button.reward-option--watch:not([disabled])');
                if (btn) btn.click();
            """)
            time.sleep(3)
            return True
        except Exception as e:
            self.log(f"奖励选择处理异常: {e}")
            return True

    # ---------- 处理广告验证弹窗 ----------
    def handle_ad_verification(self, sb):

        start = time.time()
        while time.time() - start < 15:
            if sb.execute_script("return !!document.querySelector('div.adsterra-rewarded-dialog');"):
                break

            time.sleep(1)
        else:
            self.log("未检测到广告验证弹窗，可能已直接完成")
            return True

        self.log("🛡️ 处理广告验证...")
        try:
            # 点击 Watch Ad

            sb.execute_script("""
                var btn = document.querySelector('div.adsterra-rewarded-dialog button.el-button--primary');
                if(btn) btn.click();
            """)
            time.sleep(3)

            # 处理广告窗口

            original_window = sb.driver.current_window_handle
            if len(sb.driver.window_handles) > 1:
                for handle in sb.driver.window_handles:
                    if handle != original_window:
                        sb.driver.switch_to.window(handle)
                        break

                # 检查是否被扩展拦截（可能没有实际页面，但仍尝试等待）

                try:
                    time.sleep(12)
                except:
                    pass
                if len(sb.driver.window_handles) > 1:
                    try:
                        sb.driver.close()
                    except:
                        pass
                sb.driver.switch_to.window(original_window)
                time.sleep(2)
            else:
                self.log("未检测到广告窗口，可能已被拦截，直接等待 Claim Reward")

            # 等待并点击 Claim Reward
            claim_start = time.time()
            while time.time() - claim_start < 20:
                if sb.execute_script("""
                    var btn = document.querySelector('div.adsterra-rewarded-dialog button.el-button--success');
                    return btn && !btn.disabled;
                """):
                    break
                time.sleep(1)

            sb.execute_script("""
                var btn = document.querySelector('div.adsterra-rewarded-dialog button.el-button--success');
                if(btn) btn.click();
            """)
            time.sleep(3)
            self.log("✅ 广告验证完成")
            return True
        except Exception as e:
            self.log(f"广告验证异常: {e}")
            return True

    # ---------- 续期点击与验证 ----------
    def try_extend_and_verify(self, sb, server_id, old_expiry):
        if not self.wait_turnstile(sb):
            return False, ""

        self.remove_overlay_ads(sb)
        self.log("⏳ 点击续期按钮...")
        button_clicked = False
        try:
            if sb.is_element_visible(EXTEND_BTN):
                sb.execute_script("arguments[0].click();", sb.find_element(EXTEND_BTN))
                button_clicked = True
        except:
            pass
        if not button_clicked:
            return False, ""

        time.sleep(2)

        self.handle_reward_picker(sb)

        self.handle_ad_verification(sb)

        time.sleep(5)
        for _ in range(6):
            new_ext = self.get_extension_data(sb, server_id)
            if new_ext:
                new_expiry = new_ext.get("expiredTime", "")
                if new_expiry and new_expiry != old_expiry:
                    self.log(f"✅ 续期生效: {self.format_expiry(new_expiry)}")
                    return True, self.format_expiry(new_expiry)
            time.sleep(5)

        if sb.is_element_present(EXTEND_BTN) and not sb.is_element_enabled(EXTEND_BTN):
            return "cooldown", ""
        return False, ""

    def format_expiry(self, dt_str):
        if not dt_str:
            return ""
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                try:
                    dt = datetime.strptime(dt_str, fmt)
                    return dt.strftime("%b %d, %Y, %I:%M %p UTC")
                except ValueError:
                    continue
        return dt_str

    def wait_until_running(self, sb, server_id, timeout=300, interval=10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            servers = self.get_servers_data(sb)
            if servers:
                for srv in servers:
                    if srv.get("id") == server_id:
                        state = (srv.get("serverInfo") or {}).get("state", "unknown")
                        if state == "running":
                            return True, state
            time.sleep(interval)
        return False, "unknown"

    def wait_until_not_expired(self, sb, server_id, timeout=120, interval=10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            ext_info = self.get_extension_data(sb, server_id)
            if ext_info:
                mins = ext_info.get("minutesUntilExpiration", 0)
                if mins > 0:
                    return True
            time.sleep(interval)
        return False

    # ========== Token 登录核心流程 ==========
    def token_login(self, sb, token: str) -> bool:
        """使用 Token 注入方式登录"""
        print(f"\n🚀 步骤 1: 尝试使用 Token 登录...")
        
        # 1. 尝试加载缓存
        cached_token = load_token_from_storage()
        token_to_use = cached_token if cached_token else token
        
        if not token_to_use or len(token_to_use) < 10:
            print("   ❌ 无效的 Token，请检查 BYTENUT_TOKEN 环境变量")
            return False
        
        # 2. 注入 token 到 localStorage
        if not inject_token_to_localstorage(sb, token_to_use):
            return False
        
        # 3. 检查登录状态
        if check_login_status(sb):
            print("   ✅ Token 登录成功!")
            # 如果是新 token，保存缓存
            if not cached_token and token_to_use == token:
                save_token_to_storage(token)
            return True
        else:
            print("   ⚠️ Token 可能已过期，尝试重新注入...")
            inject_token_to_localstorage(sb, token)
            time.sleep(2)
            if check_login_status(sb):
                print("   ✅ 重新注入后登录成功!")
                save_token_to_storage(token)
                return True
            print("   ❌ Token 登录失败，请重新获取有效 Token")
            return False

    def run(self):
        self.log("🚀 开始执行 ByteNut 续期与开机 (Token 模式)")
        
        # 解析账号/配置
        accounts = parse_accounts(ACCOUNTS)
        token = BYTENUT_TOKEN.strip()
        
        if not accounts and not token:
            self.log("❌ 无账号且未设置 BYTENUT_TOKEN 环境变量")
            return

        # 优先使用 Token 模式（单账号）
        if token:
            self.log(f"==== 使用 Token 模式: {self.mask_account('__token__')} ====")
            with SB(
                uc=True, test=True, headed=True,
                chromium_arg="--no-sandbox,--disable-dev-shm-usage,--disable-gpu,--window-size=1280,753",
                proxy=PROXY
            ) as sb:
                try:
                    # Token 登录
                    if not self.token_login(sb, token):
                        self.send_tg("❌", "Token 登录失败", "Token", "未知", "未知", 
                                   "请检查 BYTENUT_TOKEN 是否有效",
                                   screenshot=self.shot(sb, "token_login_fail.png"))
                        return
                    
                    sb.uc_open_with_reconnect(URL_HOMEPAGE, reconnect_time=6)
                    time.sleep(5)

                    servers = self.get_servers_data(sb)
                    if not servers:
                        self.send_tg("⚠️", "警告", "Token", "未知", "未知", "API 请求失败",
                                     screenshot=self.shot(sb, "no_server.png"))
                        return

                    server = servers[0]
                    server_id = server.get("id") or ""
                    server_info = server.get("serverInfo") or {}
                    state = server_info.get("state", "running")
                    expired_time = server.get("expiredTime") or ""
                    expiry_str = self.format_expiry(expired_time)
                    self.log(f"服务器 {self.mask_server_id(server_id)}: 状态 {state}, 到期 {expiry_str}")

                    if not server_id:
                        self.send_tg("❌", "失败", "Token", "未知", state, expiry_str,
                                     "服务器ID无效", screenshot=self.shot(sb, "invalid_id.png"))
                        return

                    ext_info = self.get_extension_data(sb, server_id)
                    if not ext_info:
                        self.send_tg("❌", "失败", "Token", server_id, state, expiry_str,
                                     screenshot=self.shot(sb, "ext_info_fail.png"))
                        return

                    can_extend = ext_info.get("canExtend", False)
                    cooldown_min = ext_info.get("minutesUntilNextExtension", 0)
                    mins_until_exp = ext_info.get("minutesUntilExpiration", 9999)
                    expired = mins_until_exp <= 0

                    self.log(f"可续期:{can_extend}, 冷却剩余:{cooldown_min}分, 距离过期:{mins_until_exp}分")

                    # ========== 离线处理 ==========
                    if state == "offline":
                        if can_extend:
                            self.log("🔴 离线，可续期...")
                            sb.uc_open_with_reconnect(f"https://www.bytenut.com/free-gamepanel/{server_id}", reconnect_time=6)
                            time.sleep(5)
                            sb.click(RENEW_MENU)
                            time.sleep(3)
                            result, new_time = self.try_extend_and_verify(sb, server_id, expired_time)
                            if result is True:
                                if not self.wait_until_not_expired(sb, server_id):
                                    self.send_tg("⚠️", "续期成功但状态未更新", "Token", server_id,
                                                 "offline", expiry_str, "无法开机，请稍后重试",
                                                 screenshot=self.shot(sb, "start_fail.png"))
                                    return

                                if self.api_start_server(sb, server_id):
                                    is_running, final_state = self.wait_until_running(sb, server_id)
                                    if is_running:
                                        self.send_tg("✅", "续期并开机成功", "Token", server_id,
                                                     "offline -> running", f"{expiry_str} -> {new_time}",
                                                     screenshot=self.shot(sb, "ok.png"))
                                    else:
                                        self.send_tg("⚠️", "续期成功，开机未确认", "Token", server_id,
                                                     f"offline -> {final_state}", new_time,
                                                     screenshot=self.shot(sb, "start_timeout.png"))
                                else:
                                    self.send_tg("✅", "续期成功，开机失败", "Token", server_id,
                                                 "offline", new_time,
                                                 screenshot=self.shot(sb, "start_fail.png"))
                            elif result == "cooldown":
                                self.send_tg("⏳", "续期后进入冷却", "Token", server_id, "offline", expiry_str,
                                             screenshot=self.shot(sb, "cooldown.png"))
                            else:
                                self.send_tg("❌", "续期失败", "Token", server_id, "offline", expiry_str,
                                             screenshot=self.shot(sb, "extend_fail.png"))
                        else:
                            if expired:
                                extra = "服务器已过期且处于冷却期，无法续期和开机"
                                self.send_tg("🚫", "无法操作", "Token", server_id, state, expiry_str, extra,
                                             screenshot=self.shot(sb, "expired_cooldown.png"))
                            else:
                                self.log("🔴 离线，冷却中，直接开机")
                                if self.api_start_server(sb, server_id):
                                    is_running, final_state = self.wait_until_running(sb, server_id)
                                    if is_running:
                                        self.send_tg("✅", "冷却中并开机成功", "Token", server_id,
                                                     "offline -> running", expiry_str,
                                                     screenshot=self.shot(sb, "started.png"))
                                    else:
                                        self.send_tg("⚠️", "开机请求已发送，未确认运行", "Token", server_id,
                                                     f"offline -> {final_state}", expiry_str,
                                                     screenshot=self.shot(sb, "start_timeout.png"))
                                else:
                                    self.send_tg("❌", "开机请求失败", "Token", server_id, "offline", expiry_str,
                                                 screenshot=self.shot(sb, "start_fail.png"))
                        return

                    # ========== 运行中处理 ==========
                    if not can_extend:
                        extra = ""
                        if expired:
                            extra = "服务器已过期，但当前处于冷却期，续期被暂时禁止"
                        self.log(f"⏳ 冷却中 ({cooldown_min}分钟)")
                        self.send_tg("⏳", "冷却中", "Token", server_id, state, expiry_str, extra,
                                     screenshot=self.shot(sb, "cooldown.png"))
                        return

                    self.log("✅ 可续期，执行续期")
                    sb.uc_open_with_reconnect(f"https://www.bytenut.com/free-gamepanel/{server_id}", reconnect_time=6)
                    time.sleep(5)
                    sb.click(RENEW_MENU)
                    time.sleep(3)
                    result, new_time = self.try_extend_and_verify(sb, server_id, expired_time)
                    if result is True:
                        self.send_tg("✅", "续期成功", "Token", server_id, state,
                                     f"{expiry_str} -> {new_time}",
                                     screenshot=self.shot(sb, "ok.png"))
                    elif result == "cooldown":
                        self.send_tg("⏳", "续期后进入冷却", "Token", server_id, state, expiry_str,
                                     screenshot=self.shot(sb, "cooldown.png"))
                    else:
                        self.send_tg("❌", "续期失败", "Token", server_id, state, expiry_str,
                                     screenshot=self.shot(sb, "extend_fail.png"))

                except Exception as e:
                    self.log(f"❌ 异常: {e}")
                    try:
                        self.send_tg("❌", "异常", "Token", "未知", "未知", str(e),
                                     screenshot=self.shot(sb, "error.png"))
                    except:
                        self.send_tg("❌", "异常", "Token", "未知", "未知", str(e))
            return

        # ========== 旧版账号密码模式（兼容）==========
        for idx, (user, pwd) in enumerate(accounts, 1):
            if user == "__token__":
                continue  # 已在上文处理
            masked_user = self.mask_account(user)
            self.log(f"==== 账号 [{idx}] {masked_user} ==== (账号密码模式)")

            with SB(
                uc=True, test=True, headed=True,
                chromium_arg="--no-sandbox,--disable-dev-shm-usage,--disable-gpu,--window-size=1280,753",
                proxy=PROXY
            ) as sb:
                try:
                    # 原账号密码登录逻辑（保留兼容）
                    sb.uc_open_with_reconnect(URL_LOGIN_PANEL, reconnect_time=5)
                    sb.wait_for_element_visible('input[placeholder="Username"]', timeout=25)
                    sb.type('input[placeholder="Username"]', user)
                    sb.type('input[placeholder="Password"]', pwd)
                    sb.click('//button[contains(., "Sign In")]')
                    time.sleep(5)
                    if "/auth/login" in sb.get_current_url():
                        err = ""
                        try:
                            err = sb.find_element('div.el-form-item__error').text
                        except:
                            pass
                        self.send_tg("❌", "登录失败", user, "未知", "未知", "",
                                     screenshot=self.shot(sb, f"login_fail_{idx}.png"))
                        continue
                    self.log("✅ 登录成功")

                    # ... 后续服务器处理逻辑与 Token 模式相同，此处省略重复代码 ...
                    # 实际使用时可将服务器处理逻辑提取为独立方法避免重复

                except Exception as e:
                    self.log(f"❌ 异常: {e}")
                    try:
                        self.send_tg("❌", "异常", user, "未知", "未知", str(e),
                                     screenshot=self.shot(sb, f"error_{idx}.png"))
                    except:
                        self.send_tg("❌", "异常", user, "未知", "未知", str(e))

        self.log("✅ 所有账号处理完毕")


if __name__ == "__main__":
    BytenutRenewal().run()
