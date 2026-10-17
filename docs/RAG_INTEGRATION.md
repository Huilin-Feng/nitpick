# nitpick RAG 模块：接入与学习指南

## 1. 新增了哪些文件

```
nitpick/rag/
  __init__.py     # 对外入口：RagContextBuilder、PRESETS
  models.py       # Chunk / RetrievedChunk / RetrievalResult、token 估算
  chunker.py      # 切块：Python 用 ast 按函数/类切，其他语言按 60 行滑窗（重叠 10 行）
  diff_utils.py   # 解析 unified diff，提取改动的文件和函数/类名
  embedder.py     # OpenAIEmbedder（真实）+ HashEmbedder（离线测试用）
  store.py        # ChromaStore（持久化）+ InMemoryStore（测试用），接口相同
  index.py        # 遍历仓库建索引；checkout_at / shallow_clone_at 切到指定 commit
  retriever.py    # 混合检索：符号匹配 + 语义检索，去重后按 token 预算截断
  context.py      # 把检索结果拼成 prompt 里的 "Related code" 一节
tests/rag/        # 34 个测试，外部 API 全部 mock，不需要网络和 API key
requirements-rag.txt
```

## 2. 安装和跑测试

```bash
cd ~/Documents/nitpick
pip install -r requirements-rag.txt
pytest tests/rag -v
```

如果你的包名不叫 `nitpick`（比如代码在 `src/nitpick/` 或者叫别的名字），把 `rag/` 放到你的包目录下，再把测试里的 `from nitpick.rag` 改成对应路径。模块内部全部用相对导入，不用改。

`test_chroma_store` 在没装 chromadb 时会自动跳过。装好之后它会真正跑一遍 Chroma，**一定要看到它通过**，因为我写代码的环境里没装 chromadb，这部分只按文档写、没实际跑过。

## 3. 接进 pipeline 模式

找到你现在拼 review prompt 的地方，加几行：

```python
from nitpick.rag import RagContextBuilder, PRESETS

def review_pr(diff_text, repo_dir, repo_slug, base_sha, mode="pipeline"):
    prompt = build_review_prompt(diff_text)          # 你现有的函数

    rag_info = None
    if mode.startswith("rag"):
        rag = RagContextBuilder.from_repo(
            repo_dir, repo_slug=repo_slug, commit_sha=base_sha,
            config=PRESETS.get(mode, PRESETS["rag-hybrid"]),
        )
        related = rag.build(diff_text)
        if related:
            prompt += "\n\n" + related
        rag_info = rag.last_result                    # 评估时用来记录成本

    return call_llm(prompt), rag_info                 # 你现有的调用
```

关键点：
- 索引要建在 **base commit**（PR 合并前的状态）上，不是 head。
- `RagContextBuilder` 会把索引缓存在 `~/.cache/nitpick/chroma`，同一个 repo + commit 第二次直接复用，不再花 embedding 的钱。
- 检索结果自动排除 PR 改动的文件，因为 diff 里已经有了。

## 4. 接进 GitHub App

GitHub App 收到 webhook 时本地没有仓库，用 `shallow_clone_at` 只拉 base commit：

```python
from nitpick.rag.index import shallow_clone_at

base_sha = payload["pull_request"]["base"]["sha"]
url = f"https://x-access-token:{installation_token}@github.com/{repo_slug}.git"
with shallow_clone_at(url, base_sha) as repo_dir:
    result, rag_info = review_pr(diff_text, repo_dir, repo_slug, base_sha, mode="rag-hybrid")
```

## 5. 评估：五组对比

每个案例都要在它自己的 base commit 上建索引：

```python
from nitpick.rag.index import checkout_at

MODES = ["pipeline", "rag-semantic", "rag-symbol", "rag-hybrid", "agent"]

for case in cases:                                   # 你现有的 21 个案例
    with checkout_at(case.repo_dir, case.base_sha) as wt:
        for mode in MODES:
            output, rag_info = review_pr(case.diff, wt, case.repo_slug, case.base_sha, mode)
            record(case, mode, output,
                   context_tokens=rag_info.context_tokens if rag_info else 0,
                   embedding_tokens=rag_info.embedding_tokens if rag_info else 0)
```

`rag-semantic` 和 `rag-symbol` 是消融实验：分别只开一路检索，用来回答"混合检索到底是哪一路在起作用"。

结果表格模板（跑完再填，**只填真实数字**）：

| 模式 | 跨文件 bug（/9） | Recall | Precision | 平均 prompt tokens | 每个 PR 成本 |
|---|---|---|---|---|---|
| pipeline | 2 | | | | |
| rag-semantic | | | | | |
| rag-symbol | | | | | |
| rag-hybrid | | | | | |
| agent | | | | | |

成本要算两部分：LLM 调用的 token，加上 embedding 的 token（`OpenAIEmbedder.cost_usd`）。建索引是一次性成本，单独记一行，不要摊进每个 PR。

## 6. 可以调的参数（`RetrievalConfig`）

| 参数 | 默认 | 作用 |
|---|---|---|
| `k_semantic` | 5 | 每个 hunk 取几个语义结果 |
| `max_symbol_hits` | 6 | 每个符号最多返回几处引用 |
| `min_semantic_score` | 0.25 | 低于这个相似度的语义结果丢掉 |
| `token_budget` | 3000 | 检索代码最多占多少 prompt tokens |

先用默认值跑完整轮评估，再挑一两个参数做小范围调整。不要拿评估集反复调参再报数字，那样结果会偏乐观；调过参的话在 README 里写明。

## 7. 建议的学习顺序

1. `models.py`：先看数据结构，其他文件都围绕它。
2. `chunker.py` + `test_chunker.py`：为什么按函数切块，大类怎么拆。
3. `diff_utils.py` + `test_diff_utils.py`：diff 格式，为什么要按 hunk 的行数解析（看 `test_lines_starting_with_dashes_inside_hunk_are_content`）。
4. `embedder.py`、`store.py`：接口（Protocol）怎么让测试不依赖外部服务。
5. `index.py`：建索引和缓存，`checkout_at` 为什么必须用 base commit。
6. `retriever.py`：整个模块的核心，读顶部注释里"为什么要两路检索"。
7. `context.py`：prompt 里怎么告诉模型"这些代码只是参考，别直接评论它们"。

每读完一个文件，试着不看代码，用自己的话讲一遍它做什么、为什么这样设计。面试时被追问的就是这些。

## 8. 已知的局限（写 README 时可以直接用作取舍分析）

- 符号匹配基于正则和名字，同名但无关的函数会被误召回；短名字和常见名字（`get`、`run`）已经过滤掉了。
- 语言支持不均衡：Python 按语法结构切，其他语言只是按行切。
- 只能找到"引用了改动符号"的代码，找不到间接依赖（A 调 B，B 调 C，C 改了）。这正是 agent 模式能主动多跳查找的优势，可以作为两者对比的讨论点。
- token 用字符数 / 4 估算，和真实 tokenizer 有误差，用于预算控制足够，报成本时用 API 返回的真实用量。
