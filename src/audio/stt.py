import whisper
import os
import torch
import warnings

from src.utils.net import setup_insecure_ssl

# 忽略 Whisper 可能产生的一些非关键警告
warnings.filterwarnings("ignore")

# 内置繁→简常用字映射（兜底用，覆盖口语高频字 + 实测出现的繁体残字）。
# 完整转换请用 OpenCC：pip install opencc-python-reimplemented
_TRAD_TO_SIMP = {
    "現": "现", "氣": "气", "麼": "么", "樣": "样", "們": "们", "說": "说",
    "話": "话", "這": "这", "來": "来", "時": "时", "間": "间", "題": "题",
    "對": "对", "會": "会", "國": "国", "個": "个", "開": "开", "關": "关",
    "東": "东", "車": "车", "學": "学", "覺": "觉", "見": "见", "視": "视",
    "聽": "听", "後": "后", "長": "长", "飛": "飞", "魚": "鱼", "馬": "马",
    "門": "门", "問": "问", "聞": "闻", "閱": "阅", "實": "实", "寫": "写",
    "認": "认", "識": "识", "讀": "读", "書": "书", "體": "体", "點": "点",
    "愛": "爱", "語": "语", "請": "请", "謝": "谢", "還": "还", "進": "进",
    "過": "过", "遠": "远", "處": "处", "裡": "里", "兩": "两", "協": "协",
    "醫": "医", "藥": "药", "園": "园", "圖": "图", "團": "团", "場": "场",
    "報": "报", "紙": "纸", "錢": "钱", "銀": "银", "電": "电", "腦": "脑",
    "網": "网", "頁": "页", "類": "类", "麵": "面", "飯": "饭", "飲": "饮",
    "館": "馆", "媽": "妈", "兒": "儿", "孫": "孙", "歲": "岁", "幾": "几",
    "廣": "广", "總": "总", "結": "结", "統": "统", "經": "经", "組": "组",
    "織": "织", "終": "终", "綠": "绿", "紅": "红", "黃": "黄", "藍": "蓝",
    "顏": "颜", "聲": "声", "響": "响", "樂": "乐", "歡": "欢", "歷": "历",
    "濕": "湿", "風": "风", "雲": "云", "觀": "观", "親": "亲", "義": "义",
    "務": "务", "勞": "劳", "動": "动", "勢": "势",
    # 扩展高频口语字（补齐上一组在常用词中仍漏掉的繁体）
    "議": "议", "錄": "录", "幫": "帮", "應": "应", "產": "产", "發": "发",
    "達": "达", "連": "连", "當": "当", "選": "选", "舉": "举", "與": "与",
    "興": "兴", "買": "买", "賣": "卖", "貴": "贵", "張": "张", "強": "强",
    "則": "则", "創": "创", "辦": "办", "續": "续", "練": "练", "細": "细",
    "維": "维", "護": "护", "讓": "让", "譯": "译", "試": "试", "詩": "诗",
    "詞": "词", "誰": "谁", "課": "课", "該": "该", "資": "资", "賽": "赛",
    "輯": "辑", "輸": "输", "轉": "转", "軟": "软", "輕": "轻", "採": "采",
    "權": "权", "構": "构", "機": "机", "極": "极", "檢": "检", "樓": "楼",
    "櫃": "柜", "殺": "杀", "復": "复", "備": "备", "條": "条", "檔": "档",
    "牆": "墙", "壞": "坏", "壯": "壮", "妝": "妆", "婦": "妇", "嬰": "婴",
    "寶": "宝", "尋": "寻", "導": "导", "壽": "寿", "專": "专", "層": "层",
    "屬": "属", "島": "岛", "師": "师", "幣": "币", "庫": "库", "廠": "厂",
    "廳": "厅", "廢": "废", "態": "态", "慮": "虑", "憂": "忧", "戰": "战",
    "戶": "户", "敵": "敌", "數": "数", "於": "于", "為": "为", "熱": "热",
    "爺": "爷", "狀": "状", "獎": "奖", "環": "环", "將": "将", "劃": "划",
    "業": "业", "軍": "军", "農": "农", "華": "华", "雜": "杂", "離": "离",
    "難": "难", "願": "愿", "獲": "获", "標": "标", "橋": "桥", "歸": "归",
    "齡": "龄", "齒": "齿", "龍": "龙", "龜": "龟",
    # 再补一批极高频字（记/尽/错/别 等）
    "記": "记", "盡": "尽", "錯": "错", "別": "别", "單": "单", "員": "员",
    "職": "职", "劇": "剧", "剛": "刚", "區": "区", "陽": "阳", "陰": "阴",
    "際": "际", "陸": "陆", "險": "险", "隨": "随", "階": "阶", "顧": "顾",
    "鹽": "盐", "頭": "头", "顯": "显", "禮": "礼", "規": "规", "觸": "触",
    "計": "计", "訂": "订", "許": "许", "設": "设", "訪": "访", "評": "评",
    "調": "调", "談": "谈", "論": "论", "質": "质", "販": "贩", "費": "费",
    "責": "责", "貼": "贴", "貿": "贸", "貸": "贷",
    # 收尾高频字
    "沒": "没", "簡": "简", "測": "测", "試": "试", "紹": "绍",
    "絡": "络", "給": "给", "緒": "绪", "綱": "纲", "納": "纳",
}

# OpenCC 可用性（模块级检测一次，供 __init__ 打印状态，避免每次转录都尝试 import）
_OPENCC_AVAILABLE = None


def _check_opencc():
    """检测 OpenCC 是否可用（完整繁→简转换）。

    仅在模块内调用一次并缓存结果，避免每次转录都 import 探测。
    返回 True 表示可用（完整转换），False 表示回退内置字表。
    """
    global _OPENCC_AVAILABLE
    if _OPENCC_AVAILABLE is not None:
        return _OPENCC_AVAILABLE
    try:
        from opencc import OpenCC  # noqa: F401  仅探测可用性
        _OPENCC_AVAILABLE = True
    except Exception:
        _OPENCC_AVAILABLE = False
    return _OPENCC_AVAILABLE


def _pick_device():
    """选择设备"""
    if torch.cuda.is_available():
        return "cuda"
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and mps.is_available():
        return "mps"
    return "cpu"


class SpeechRecognizer:
    """
    语音识别类 (STT)
    使用 OpenAI Whisper 模型将音频转换为文本。
    """
    def __init__(self, model_size="base", language=None):
        """
        初始化识别器

        Args:
            model_size (str): 模型大小，可选 'tiny', 'base', 'small', 'medium', 'large'
                              对于笔记本，推荐 'base' 或 'small'。
            language (str|None): 强制识别语言。默认读环境变量 STT_LANGUAGE（缺省 'zh'）。
                                 显式锁定可避免 Whisper 自动检测漂移导致的繁体/乱码。
        """
        # 默认强制简体中文：Whisper 自动检测在 tiny 模型上易漂移，
        # 一旦误判为繁中会吐出“現在天氣怎麼樣”这类繁体或乱码（已实测）。
        if language is None:
            language = os.environ.get("STT_LANGUAGE", "zh")
        self.language = language
        print(f"[系统] 正在加载 Whisper 模型 ({model_size})... 这可能需要几分钟...")
        # 任何下载前先应用 SSL 设置（代理自签证书环境需要 JAC_HF_INSECURE=1）
        setup_insecure_ssl()
        try:
            # 选择加速设备：cuda > mps(Mac GPU) > cpu
            device = _pick_device()
            print(f"[系统] 运行设备: {device}")
            
            # 加载模型（首次会从网络下载权重到 ~/.cache/whisper/，之后复用本地缓存）
            self.model = whisper.load_model(model_size, device=device)
            print("[系统] Whisper 模型加载成功！")
            # 打印繁→简转换引擎状态：OpenCC 提供完整转换，未安装则回退内置字表兜底
            if _check_opencc():
                print("[系统] 繁→简转换引擎：OpenCC 已启用（完整转换）。识别结果将直接以简体中文交给大脑。")
            else:
                print("[系统] 繁→简转换引擎：未安装 OpenCC，使用内置常用字表兜底（可能存在漏字）。"
                      "建议安装：pip install opencc-python-reimplemented")
        except Exception as e:
            print(f"[错误] Whisper 模型加载失败: {e}")
            print("[提示] 若报 CERTIFICATE_VERIFY_FAILED（代理自签证书环境），请先执行：")
            print("        export JAC_HF_INSECURE=1   # 关闭 SSL 校验（仅可信内网）")
            print("       然后再启动；或手动预下载权重到 ~/.cache/whisper/ 以彻底离线。")
            self.model = None

    def transcribe(self, audio_data):
        """
        识别音频数据

        Args:
            audio_data: 这里的输入取决于具体的录音实现。
                        Whisper 通常接受文件路径或 numpy 数组。
                        为了兼容性，我们这里假设传入的是一个音频文件路径。

        Returns:
            text (str): 识别出的文本（已强制为简体中文）
        """
        if self.model is None:
            return ""

        try:
            # transcribe 方法可以直接处理文件路径
            # fp16=False 是为了兼容 CPU (CPU 不支持半精度浮点数)
            # language=self.language：强制锁定语言，根治自动检测漂移导致的繁体/乱码
            result = self.model.transcribe(audio_data, fp16=False, language=self.language)
            text = result['text'].strip()
            # 兜底归一化：即便模型仍偶发繁体残字，也转成简体，保证下游一致
            text = self._to_simplified(text)
            return text
        except Exception as e:
            print(f"[错误] 语音识别出错: {e}")
            return ""

    def _to_simplified(self, text):
        """
        将文本中的繁体中文归一化为简体中文（防御性兜底）。

        优先调用 opencc（完整、准确）；若未安装则走内置常用字映射；
        两者都不可用时原样返回。这样不强制引入新依赖，避免国内网络安装失败。
        """
        if not text:
            return text
        # 1) 优先用 OpenCC 做完整转换（需 pip install opencc-python-reimplemented）
        if _OPENCC_AVAILABLE:
            try:
                from opencc import OpenCC
                return OpenCC("t2s").convert(text)
            except Exception:
                pass
        # 2) 内置常用繁→简映射兜底（覆盖口语高频字，含已实测出现的繁体残字）
        return "".join(_TRAD_TO_SIMP.get(ch, ch) for ch in text)

if __name__ == "__main__":
    # 测试代码
    # 需要有一个 test.wav 文件才能运行
    print("请配合 recorder.py 进行测试")
