# 🎯 三种方案对比 - 选择最适合你的

## 方案对比

| 特性 | 1️⃣ Ollama (本地) | 2️⃣ DeepSeek (优化版) | 3️⃣ Groq (在线) |
|------|-----------------|---------------------|----------------|
| **成本** | ✅ 完全免费 | ⚠️ 约 ¥0.01/局 | ✅ 免费（需注册） |
| **速度** | ⭐⭐⭐⭐ 快 | ⭐⭐⭐ 中等 | ⭐⭐⭐⭐⭐ 极快 |
| **稳定性** | ✅ 无错误 | ⚠️ 偶尔工具调用错误 | ✅ 完美 |
| **设置难度** | ⭐⭐ 需安装软件 | ⭐ 最简单 | ⭐⭐⭐ 需注册 |
| **网络要求** | ✅ 无需网络 | ❌ 需要稳定网络 | ❌ 需要网络 |
| **隐私** | ✅ 完全本地 | ⚠️ 数据上传 | ⚠️ 数据上传 |
| **推荐度** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ |

## 🎯 选择建议

### 👉 强烈推荐：Ollama (本地免费)

**适合你，如果：**
- ✅ 电脑有 8GB+ 内存
- ✅ 想完全免费
- ✅ 关心数据隐私
- ✅ 网络不稳定

**设置步骤：**
```powershell
# 1. 下载安装 https://ollama.com/download/windows
# 2. 拉取模型
ollama pull qwen2.5:7b

# 3. 运行游戏
$env:WEREWOLF_MODEL = "ollama"
$env:OLLAMA_MODEL = "qwen2.5:7b"
python main.py
```

**优势：**
- 💰 完全免费，无限使用
- 🚀 本地运行，响应快速
- 🔒 数据不出本地，完全隐私
- ✅ 无工具调用问题

---

### 👉 备选方案：DeepSeek (优化版)

**适合你，如果：**
- ✅ 电脑配置不够
- ✅ 不想安装软件
- ✅ 能接受少量成本（约¥0.5/天）

**当前配置：**
- 已默认配置 DeepSeek
- 已添加错误过滤和自动重试
- 已精简 prompt 减少 token 消耗

**直接运行：**
```powershell
python main.py
```

**注意事项：**
- ⚠️ 可能出现工具调用警告（已自动修复）
- ⚠️ 需要保持网络连接
- ⚠️ API 余额不足时会失败

---

### 👉 最佳但需注册：Groq

**如果你能注册成功：**
```powershell
$env:WEREWOLF_MODEL = "groq"
$env:GROQ_API_KEY = "gsk_你的key"
python main.py
```

**为什么最好：**
- 完全免费 + 速度最快 + 无任何错误

**注册问题可能的解决方案：**
1. 使用 VPN/代理
2. 使用 Google/GitHub 账号登录
3. 换个浏览器/清除缓存
4. 使用手机热点

---

## 💡 我的推荐

### 优先级排序：

1. **Ollama (本地)** - 如果你的电脑够用 → 长期最佳
2. **Groq** - 如果能注册 → 在线最佳
3. **DeepSeek** - 快速开始 → 临时方案

---

## 📝 快速决策表

### 你的电脑内存是多少？

- **16GB+** → 使用 Ollama `qwen2.5:14b`（性能最强）
- **8-16GB** → 使用 Ollama `qwen2.5:7b`（推荐）
- **4-8GB** → 使用 Ollama `llama3.2:3b`（最小可用）
- **<4GB** → 使用 DeepSeek（在线方案）

### 你的网络如何？

- **不稳定/无网** → 必须用 Ollama
- **稳定** → 任意选择

### 你的优先考虑？

- **成本** → Ollama 或 Groq（都免费）
- **速度** → Groq（最快） 或 Ollama（本地快）
- **简单** → DeepSeek（已配置好）
- **隐私** → Ollama（本地）

---

## 🚀 立即开始

### 最简单方式（DeepSeek）：
```powershell
cd "d:\桌面\开源软件\agent\-agent\examples\game\werewolves"
python main.py
```
现在就能跑，但会有警告。

### 推荐方式（Ollama）：
```powershell
# 1. 安装 Ollama：https://ollama.com/download/windows
# 2. 下载模型（一次性，约 5 分钟）
ollama pull qwen2.5:7b

# 3. 运行游戏
$env:WEREWOLF_MODEL = "ollama"
$env:OLLAMA_MODEL = "qwen2.5:7b"
python main.py
```

---

## ❓ 你想要哪种方案？

1. **Ollama** - 我帮你验证安装
2. **DeepSeek** - 继续用当前配置
3. **其他** - 我再找方案

告诉我你的选择，我帮你配置到最佳状态！
