import streamlit as st
import base64
from zhipuai import ZhipuAI
from openai import OpenAI
from PIL import Image, ImageOps, ImageEnhance
import io
import json
from github import Github, InputFileContent
import uuid
import time
import extra_streamlit_components as stx
from datetime import datetime, timedelta
import gc # 引入垃圾回收机制

# ================= 1. 页面基础配置 =================
st.set_page_config(
    page_title="赛博学霸 Pro",
    page_icon="🧬",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# 自定义 CSS
st.markdown("""
<style>
    .main-title {font-size: 2.2rem; color: #FFD700; text-align: center; font-weight: bold; text-shadow: 0 0 10px rgba(255, 215, 0, 0.5);}
    .sub-title {font-size: 1rem; color: #B0BEC5; text-align: center; margin-bottom: 20px;}
    .answer-area {background-color: #1E1E1E; padding: 20px; border-radius: 8px; border-left: 5px solid #FFD700; color: #E0E0E0; font-family: sans-serif; line-height: 1.6;}
    [data-testid="stSidebar"] {background-color: #121212 !important; color: #FFFFFF !important;}
    .stTextInput input {background-color: #2C2C2C !important; color: #FFFFFF !important;}
    
    /* 隐藏图片上传后的默认文件名，让界面更清爽 */
    .uploadedFile {display: none;}
</style>
""", unsafe_allow_html=True)

# ================= 2. 核心配置 =================
GITHUB_TOKEN = st.secrets.get("GITHUB_TOKEN", "") 
GIST_ID = st.secrets.get("GIST_ID", "")
ZHIPU_KEY = st.secrets.get("ZHIPU_KEY", "")
DEEPSEEK_KEY = st.secrets.get("DEEPSEEK_KEY", "")
MY_WECHAT = "Liao_Code_Master"

# ================= 3. 科目表 =================
SUBJECT_TASKS = {
    "高等数学": ["极限与连续求解", "导数与微分推导", "不定积分/定积分", "微分方程求解", "级数收敛性判定"],
    "线性代数": ["矩阵运算与求逆", "行列式计算", "向量组与秩", "特征值与特征向量", "二次型化简"],
    "概率统计": ["分布函数分析", "期望与方差计算", "参数估计", "假设检验"],
    "模拟电路": ["二极管/三极管电路分析", "运算放大器计算", "反馈电路类型判断", "频率响应分析"],
    "数字电路": ["逻辑门电路分析", "组合逻辑设计", "时序逻辑(触发器)", "A/D与D/A转换"],
    "计算机/408": ["数据结构算法手写", "操作系统原理", "计算机网络协议", "计算机组成架构"],
    "大学物理": ["力学受力分析", "电磁学计算", "光学原理", "热力学定律"],
    "考研英语": ["长难句语法切分", "英一/英二作文批改", "阅读逻辑分析", "翻译精讲 (信达雅)"],
    "考研政治": ["马原原理辨析", "毛中特考点", "史纲时间线梳理", "时政热点分析"]
}

# ================= 4. Cookie =================
cookie_manager = stx.CookieManager(key="mobile_cookie_v3_6")

# ================= 5. 验证逻辑 =================
def connect_db():
    try:
        g = Github(GITHUB_TOKEN)
        gist = g.get_gist(GIST_ID)
        file = gist.files['licenses.json']
        return json.loads(file.content), gist
    except: return None, None

def get_device_id():
    if 'device_id' not in st.session_state:
        st.session_state.device_id = str(uuid.uuid4())
    return st.session_state.device_id

def activate_license(license_key):
    if not license_key: return False, "请输入卡密"
    db, gist = connect_db()
    if not db: return False, "云端连接失败"
    if license_key not in db: return False, "❌ 卡密不存在"
    
    record = db[license_key]
    current_device = get_device_id()
    
    if record['status'] == 'UNUSED':
        valid_days = record.get('valid_days', 365)
        expire_date = (datetime.now() + timedelta(days=valid_days)).strftime("%Y-%m-%d")
        db[license_key]['status'] = 'USED'
        db[license_key]['bind_device'] = current_device
        db[license_key]['activated_at'] = time.strftime("%Y-%m-%d %H:%M:%S")
        db[license_key]['expire_at'] = expire_date
        try: gist.edit(files={'licenses.json': InputFileContent(json.dumps(db, indent=2))})
        except: pass 
        try:
            expires = datetime.now() + timedelta(days=valid_days)
            cookie_manager.set('user_license', license_key, expires_at=expires, key="set_lic")
        except: cookie_manager.set('user_license', license_key, key="set_lic")
        return True, f"✅ 激活成功！有效期至：{expire_date}"
    elif record['status'] == 'USED':
        expire_date_str = record.get('expire_at', '2099-12-31')
        if datetime.now().strftime("%Y-%m-%d") > expire_date_str: return False, "⚠️ 卡密已过期"
        cookie_manager.set('user_license', license_key, key="set_lic")
        return True, f"✅ 欢迎回来"
    return False, "❌ 状态异常"

def auto_login_check():
    if st.session_state.get('force_logout', False): return False, None
    if st.session_state.get('is_vip', False): return True, st.session_state.get('user_license', '')
    try:
        cookies = cookie_manager.get_all()
        c_license = cookies.get('user_license')
        if c_license and len(c_license) > 5:
            st.session_state['is_vip'] = True
            st.session_state['user_license'] = c_license
            return True, c_license
    except: pass
    return False, None

# ================= 6. 图像处理与AI (内存优化版) =================

def process_image_safe(image_file):
    """安全处理：压缩与增强，防止内存溢出"""
    try:
        image_file.seek(0)
        img_obj = Image.open(image_file)
        
        # 1. 修正旋转 (手机拍照常见问题)
        img_obj = ImageOps.exif_transpose(img_obj)
        
        # 2. 强力压缩：将宽/高限制在 1200px 以内
        # 1200px 对于 OCR 足够清晰，但内存占用只有原图的 1/10
        img_obj.thumbnail((1200, 1200))
        
        # 3. 增强对比度 (弥补压缩损失)
        enhancer = ImageEnhance.Contrast(img_obj)
        img_obj = enhancer.enhance(1.5)
        
        return img_obj
    except Exception as e:
        st.error(f"图片处理失败: {e}")
        return None

def ocr_general(image_obj, subject):
    if not ZHIPU_KEY: return "Error: Key未配置"
    client = ZhipuAI(api_key=ZHIPU_KEY)
    
    buffered = io.BytesIO()
    # 存为 JPEG，质量 80，进一步省内存
    image_obj.save(buffered, format="JPEG", quality=80) 
    img_base64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
    
    prompt = f"""
    你是一个专业的学术OCR助手。请精准识别图片中的【{subject}】内容。
    【要求】：
    1. 所见即所得：直接输出识别内容。
    2. 数学公式请使用 Markdown 格式（$符号包裹 LaTeX）。
    """
    try:
        res = client.chat.completions.create(
            model="glm-4v",
            messages=[{"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": img_base64}}]}]
        )
        return res.choices[0].message.content
    except: return "图片识别失败"

def ai_tutor_brain(question_text, subject, task_type):
    if not DEEPSEEK_KEY: return "Error: Key未配置"
    client = OpenAI(api_key=DEEPSEEK_KEY, base_url="https://api.deepseek.com")
    
    strategy = "请进行深入的原理分析，逻辑必须严密。"
    if "推导" in task_type: strategy = "请列出详细的推导步骤。"
    
    system_prompt = f"""
    你是一位【{subject}】领域的顶尖教授。当前任务：{task_type}。
    【最高指令】：
    1. **深度优先**：深入底层原理。
    2. **格式规范**：数学公式用 $ 包裹 LaTeX，重点加粗。
    【教学策略】：{strategy}
    """
    try:
        res = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"题目：\n{question_text}\n\n请教授讲解。"}
            ],
            temperature=0.2
        )
        return res.choices[0].message.content
    except Exception as e: return f"AI思考失败: {str(e)}"

# ================= 7. 界面逻辑 =================

is_logged_in, current_user = auto_login_check()

with st.sidebar:
    st.markdown("## 🔐 赛博学霸通行证")
    if is_logged_in:
        st.success(f"🟢 已登录")
        if st.button("🚪 安全退出", type="secondary", use_container_width=True):
            try: cookie_manager.delete('user_license')
            except: pass
            st.session_state['is_vip'] = False
            st.session_state['force_logout'] = True
            st.warning("退出中...")
            time.sleep(1)
            st.rerun()
    else:
        license_input = st.text_input("请输入专属卡密", type="password")
        if st.button("🚀 登录 / 激活", type="primary", use_container_width=True):
            with st.spinner("验证中..."):
                valid, msg = activate_license(license_input)
                if valid:
                    st.success(msg)
                    st.session_state['is_vip'] = True
                    st.session_state['force_logout'] = False
                    st.session_state['user_license'] = license_input
                    time.sleep(1) 
                    st.rerun()
                else:
                    st.error(msg)
    st.divider()
    with st.expander("💎 开通会员", expanded=True):
        st.info("扫码支付后，截图加微信领卡密")
        # 略过图片加载

# 主界面
st.markdown("<div class='main-title'>🧬 赛博学霸 Pro</div>", unsafe_allow_html=True)
st.markdown("<div class='sub-title'>DeepSeek × GLM-4V | 大学生/考研/科研 AI 助手</div>", unsafe_allow_html=True)

if is_logged_in:
    with st.container(border=True):
        # 手机端把科目选择放在上面
        subject = st.selectbox("📚 选择专业", list(SUBJECT_TASKS.keys()))
        task = st.selectbox("📝 选择模式", SUBJECT_TASKS[subject])
    
    # 📸 极简上传模块 (防闪退核心)
    st.info("💡 **提示**：点击下方按钮 -> 选择【相机】拍摄更清晰。")
    
    # 只保留一个入口，减少混淆
    uploaded_file = st.file_uploader("📤 点击拍摄/上传题目", type=["jpg", "png", "jpeg"], label_visibility="collapsed")

    if uploaded_file:
        st.markdown("---")
        
        # 🔥 核心改变：不直接显示大图！只显示文件名和大小
        # 这样浏览器就不会去渲染 10MB 的图片，从而避免闪退
        file_size_mb = uploaded_file.size / (1024 * 1024)
        st.success(f"✅ 图片已接收 ({file_size_mb:.2f} MB)")
        
        # 按钮也是大大的，方便点击
        if st.button("🚀 立即开始分析", type="primary", use_container_width=True):
            
            # 进度条
            progress = st.progress(0)
            status = st.empty()
            
            # Step 1: 后台静默处理图片
            status.write("⚙️ 正在优化图像画质...")
            img_obj = process_image_safe(uploaded_file)
            
            if img_obj:
                # 此时图片已经变小了，可以安全地展示一个小缩略图给用户看一眼
                st.image(img_obj, caption="图像已增强", width=300) # 限制宽度
                
                # Step 2: OCR
                status.write("👀 视觉引擎正在提取信息...")
                progress.progress(30)
                ocr_text = ocr_general(img_obj, subject)
                
                # 内存回收
                del img_obj
                gc.collect()
                
                # Step 3: DeepSeek
                if "失败" not in ocr_text:
                    status.write(f"🧠 教授正在推导逻辑...")
                    progress.progress(70)
                    ai_result = ai_tutor_brain(ocr_text, subject, task)
                    
                    progress.progress(100)
                    status.empty()
                    
                    with st.expander("🔍 查看识别的题目文本"):
                        st.text(ocr_text)
                    
                    st.markdown(f"### 👩‍🏫 教授详细解析")
                    with st.container(border=True):
                        st.markdown(ai_result)
                    st.balloons()
                else:
                    st.error("识别失败，请尝试重新拍摄更清晰的照片。")
            else:
                st.error("图片处理失败，请重试。")
else:
    st.info("👋 欢迎！请在左侧输入卡密登录。")