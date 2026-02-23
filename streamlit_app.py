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
import gc

# ================= 1. 页面基础配置 =================
st.set_page_config(
    page_title="赛博学霸 Pro",
    page_icon="🧬",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 自定义 CSS (精简版：只美化标题和答案区，不乱改按钮和背景)
st.markdown("""
<style>
    .main-title {
        font-size: 2.5rem; 
        color: #0F52BA; /* 宝马蓝，专业且清晰 */
        text-align: center; 
        font-weight: bold;
    }
    .sub-title {
        font-size: 1.1rem; 
        color: #555; 
        text-align: center; 
        margin-bottom: 20px;
    }
    .answer-area {
        background-color: #F0F2F6; 
        padding: 20px; 
        border-radius: 10px; 
        border-left: 5px solid #0F52BA; 
        color: #31333F;
        font-family: sans-serif;
        line-height: 1.6;
    }
    /* 隐藏部分干扰元素 */
    .stDeployButton {display:none;}
</style>
""", unsafe_allow_html=True)

# ================= 2. 核心配置 =================
GITHUB_TOKEN = st.secrets.get("GITHUB_TOKEN", "") 
GIST_ID = st.secrets.get("GIST_ID", "")
ZHIPU_KEY = st.secrets.get("ZHIPU_KEY", "")
DEEPSEEK_KEY = st.secrets.get("DEEPSEEK_KEY", "")
MY_WECHAT = "liao13689209126"

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
cookie_manager = stx.CookieManager(key="cookie_fix_v3_7")

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

# ================= 6. 图像处理与AI =================

def process_image_safe(image_file):
    """安全处理：压缩与增强，防止内存溢出"""
    try:
        # 重置文件指针，防止读取错误
        image_file.seek(0)
        img_obj = Image.open(image_file)
        
        # 1. 修正旋转
        img_obj = ImageOps.exif_transpose(img_obj)
        
        # 2. 格式转换：强制转为 RGB，防止 PNG 透明通道报错
        if img_obj.mode != 'RGB':
            img_obj = img_obj.convert('RGB')
        
        # 3. 强力压缩：将宽/高限制在 1200px 以内
        img_obj.thumbnail((1200, 1200))
        
        # 4. 增强对比度
        enhancer = ImageEnhance.Contrast(img_obj)
        img_obj = enhancer.enhance(1.5)
        
        return img_obj
    except Exception as e:
        return None

def ocr_general(image_obj, subject):
    if not ZHIPU_KEY: return "Error: Key未配置"
    client = ZhipuAI(api_key=ZHIPU_KEY)
    
    buffered = io.BytesIO()
    image_obj.save(buffered, format="JPEG", quality=85) 
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

def load_image(path):
    import os
    if os.path.exists(path): return Image.open(path)
    return None

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
    
    # 💎 修复：确保二维码显示
    with st.expander("💎 开通会员 (查看价格)", expanded=True):
        st.markdown("""
        | 套餐类型 | 价格 | 适用 |
        | :--- | :--- | :--- |
        | **⚡ 体验卡** | **¥ 9.9** | 临时 |
        | **📅 学霸月卡** | **¥ 49.9** | 冲刺 |
        | **🥇 硕博年卡** | **¥ 299** | 长期 |
        """)
        
        pay_method = st.radio("支付方式:", ["微信支付", "支付宝"], horizontal=True)
        try:
            if pay_method == "微信支付":
                st.image("pay_wechat.png", caption="请备注：手机号")
            else:
                st.image("pay_alipay.png", caption="请备注：手机号")
        except:
            st.warning("请检查图片是否上传至GitHub根目录")
            
        st.markdown(f"**客服微信**: `{MY_WECHAT}`")

# 主界面
st.markdown("<div class='main-title'>🧬 赛博学霸 Pro</div>", unsafe_allow_html=True)
st.markdown("<div class='sub-title'>DeepSeek × GLM-4V | 大学生/考研/科研 AI 助手</div>", unsafe_allow_html=True)

if is_logged_in:
    with st.container(border=True):
        c1, c2 = st.columns(2)
        with c1:
            subject = st.selectbox("📚 选择专业", list(SUBJECT_TASKS.keys()))
        with c2:
            task = st.selectbox("📝 选择模式", SUBJECT_TASKS[subject])
    
    # ================= 📸 修复版上传模块 (双保险) =================
    st.markdown("### 📤 上传题目")
    
    # 使用 Tabs，把选择权还给用户
    # Tab 1: 浏览文件 (有些手机能调起相机，有些是相册)
    # Tab 2: 网页相机 (必杀技，一定能拍照)
    tab1, tab2 = st.tabs(["📂 相册/文件 (推荐)", "📸 网页相机 (备用)"])
    
    final_image = None
    
    with tab1:
        uploaded_file = st.file_uploader(
            "选择图片 (支持高清图)", 
            type=["jpg", "png", "jpeg"], 
            key="uploader_tab1"
        )
        if uploaded_file: final_image = uploaded_file
        
    with tab2:
        camera_file = st.camera_input("点击下方按钮拍照")
        if camera_file: final_image = camera_file

    if final_image:
        st.markdown("---")
        
        # 预处理图片
        img_obj = process_image_safe(final_image)
        
        if img_obj:
            # 显示小图，防止内存溢出
            st.image(img_obj, caption="✅ 图片已就绪", width=300)
            
            if st.button("🚀 启动科研引擎", type="primary", use_container_width=True):
                progress = st.progress(0)
                status = st.empty()
                
                status.write("👀 视觉引擎正在提取信息...")
                progress.progress(30)
                
                ocr_text = ocr_general(img_obj, subject)
                
                if "失败" not in ocr_text:
                    status.write(f"🧠 教授正在推导逻辑...")
                    progress.progress(70)
                    ai_result = ai_tutor_brain(ocr_text, subject, task)
                    
                    progress.progress(100)
                    status.empty()
                    
                    # 结果展示区 (恢复正常配色)
                    with st.expander("🔍 原始识别文本", expanded=False):
                        st.text(ocr_text)
                    
                    st.markdown(f"### 👩‍🏫 教授详细解析")
                    with st.container(border=True):
                        st.markdown(ai_result)
                    st.balloons()
                else:
                    st.error("识别失败，图片可能太模糊。")
        else:
            st.error("图片处理出错，请重试。")
            
else:
    st.info("👋 欢迎！请在左侧输入卡密登录。")
    st.markdown("""
    ### 🚀 为什么你需要赛博学霸？
    - **硬核学科**：高数、线代、模电、408... 
    - **深度推导**：拒绝只有答案，提供完整推导过程。
    - **考研神器**：随时随地的私人教授。
    """)