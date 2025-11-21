# 🚀 快速解决工具调用错误 - 切换到 Groq

## 问题根源
DeepSeek 模型被训练成自动输出工具调用格式（tool_use），即使 prompt 明确禁止。这是模型本身的问题，无法通过代码完全修复。

## ✅ 推荐解决方案：切换到 Groq

### 为什么选择 Groq？
1. **完全免费** - 每天有充足的免费额度
2. **极快速度** - 比 DeepSeek 快 5-10 倍
3. **不输出工具调用** - 使用标准 OpenAI 格式，不会有 tool_use 问题
4. **高质量** - Llama 3.3 70B 模型性能优秀

### 快速配置步骤

#### 1. 获取 Groq API Key（免费）
访问：https://console.groq.com/keys
- 注册账号（支持 Google 登录）
- 创建 API Key
- 复制 Key（格式：`gsk_...`）

#### 2. 设置环境变量并运行

```powershell
# 设置使用 Groq
$env:WEREWOLF_MODEL = "groq"

# 设置你的 Groq API Key（替换为你的实际 Key）
$env:GROQ_API_KEY = "gsk_your_api_key_here"

# 运行游戏
python main.py
```

#### 3. 永久配置（可选）

在 PowerShell 配置文件中添加：
```powershell
# 编辑配置文件
notepad $PROFILE

# 添加以下内容
$env:WEREWOLF_MODEL = "groq"
$env:GROQ_API_KEY = "gsk_your_api_key_here"
```

## 🔄 其他免费选项

### 选项 2: Ollama（本地运行，完全免费）
```powershell
# 1. 下载安装 Ollama: https://ollama.com/download
# 2. 拉取模型
ollama pull qwen2:7b

# 3. 运行游戏
$env:WEREWOLF_MODEL = "ollama"
python main.py
```

**优点**：完全免费，无需 API Key，数据不出本地
**缺点**：需要较好的 GPU（建议 8GB+ 显存）

### 选项 3: 继续使用 DeepSeek（需要容忍错误）
如果你必须使用 DeepSeek：
- 代码已添加错误过滤和重试机制
- 大部分情况能正常工作，但会有警告信息
- 某些回合可能需要重试

## 📊 性能对比

| 模型 | 成本 | 速度 | 工具调用问题 | 推荐度 |
|------|------|------|--------------|--------|
| **Groq (Llama 3.3)** | 免费 | 极快 | ✅ 无 | ⭐⭐⭐⭐⭐ |
| Ollama (本地) | 免费 | 快 | ✅ 无 | ⭐⭐⭐⭐ |
| DeepSeek | 便宜 | 中等 | ❌ 有 | ⭐⭐⭐ |
| DashScope | 有限免费 | 快 | ✅ 无 | ⭐⭐⭐⭐ |

## 🎯 我的建议

**立即切换到 Groq**，它解决了所有问题：
1. 去 https://console.groq.com/keys 获取 Key（30秒）
2. 设置环境变量（10秒）
3. 运行游戏，享受流畅体验！

不需要等 DeepSeek 修复，Groq 就是完美方案。
