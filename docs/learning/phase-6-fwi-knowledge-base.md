# Phase 6: FWI 本地知识库 — 学习文档

## 一、目标

建立本地知识资源目录，放入经典 FWI 理论内容，实现关键词检索，为后续 RAG 检索预留接口。

## 二、设计思路

### 2.1 为什么需要本地知识库

**原来的问题**:

FWITheoryAgent 只依赖 LLM 的内部知识回答问题。

**痛点分析**:

| 痛点 | 具体问题 | 影响 |
|------|----------|------|
| **知识有限** | LLM 训练数据有截止日期 | 无法回答最新研究 |
| **不够专业** | LLM 可能产生幻觉 | 回答可能不准确 |
| **无法引用** | 无法给出具体文献来源 | 学术可信度低 |
| **团队知识** | 课题组积累的知识无法沉淀 | 知识无法共享 |

**解决方案**: 建立本地知识库，让 Agent 能够引用具体文献和资料。

### 2.2 知识库目录结构

```
resources/
├── fwi_knowledge/        # FWI 理论（Markdown）
│   ├── fwi_basics.md     # FWI 基础理论
│   ├── cycle_skipping.md # Cycle skipping 问题
│   ├── adjoint_state.md  # 伴随状态法
│   ├── multiscale_fwi.md # 多尺度反演
│   └── awi.md            # 自适应波形反演
├── fwi_models/           # 速度模型 metadata（JSON）
│   └── model_metadata.json
├── fwi_datasets/         # 数据集 metadata（JSON）
│   └── dataset_metadata.json
└── fwi_notes/            # 研究笔记（Markdown）
```

### 2.3 知识库内容

#### 经典 FWI 理论文献

| 文献 | 年份 | 贡献 |
|------|------|------|
| Tarantola | 1984 | 提出 FWI 理论框架 |
| Tarantola | 1986 | 弹性波 FWI |
| Pratt & Worthington | 1990 | 频率域 FWI |
| Bunks et al. | 1995 | 多尺度 FWI |
| Virieux & Operto | 2009 | FWI 综述 |
| Warner & Guasch | 2016 | 自适应波形反演 AWI |

#### 经典速度模型

| 模型 | 特点 | 用途 |
|------|------|------|
| Marmousi-2 | 复杂构造、盐丘 | FWI 算法验证 |
| Overthrust | 逆冲断层 | 复构成像测试 |
| Salt 2D | 盐丘模型 | 高速体成像测试 |

## 三、技术实现详解

### 3.1 KnowledgeBase 类

```cpp
// orchestrator/include/agent_rpc/orchestrator/knowledge_base.h

/**
 * @brief Knowledge document
 */
struct KnowledgeDocument {
    std::string path;           // 文件路径
    std::string title;          // 文档标题
    std::string content;        // 完整内容
    std::string category;       // 分类 (knowledge/models/datasets/notes)
    float relevance_score;      // 相关度分数
};

/**
 * @brief Local knowledge base
 *
 * 加载和搜索本地 Markdown/JSON 知识文件。
 * 支持基于关键词的搜索和相关度评分。
 */
class KnowledgeBase {
public:
    /**
     * @brief 从目录加载知识库
     * @param resource_dir 资源根目录
     * @return true 如果加载成功
     */
    bool load(const std::string& resource_dir) {
        resource_dir_ = resource_dir;

        // 加载所有分类
        load_directory(resource_dir + "/fwi_knowledge", "knowledge");
        load_directory(resource_dir + "/fwi_models", "models");
        load_directory(resource_dir + "/fwi_datasets", "datasets");
        load_directory(resource_dir + "/fwi_notes", "notes");

        return !documents_.empty();
    }

    /**
     * @brief 搜索知识库
     * @param query 搜索查询
     * @param topK 最大结果数
     * @return 按分数排序的相关文档
     */
    std::vector<KnowledgeDocument> search(const std::string& query, int topK = 3);

    /**
     * @brief 读取指定文件
     * @param path 文件路径（相对于 resource_dir）
     * @return 文件内容
     */
    std::string read(const std::string& path);

private:
    /**
     * @brief 计算相关度分数
     *
     * 评分因素:
     * - 标题匹配: +0.5 per keyword
     * - 内容关键词匹配: +0.05 per occurrence (max 0.3)
     * - 分类加成: +0.2 if category matches query
     */
    float compute_relevance(const std::string& lower_query,
                           const std::vector<std::string>& keywords,
                           const KnowledgeDocument& doc);

    std::string resource_dir_;
    std::vector<KnowledgeDocument> documents_;
};
```

### 3.2 相关度计算

```cpp
float compute_relevance(const std::string& lower_query,
                       const std::vector<std::string>& keywords,
                       const KnowledgeDocument& doc) {
    float score = 0.0f;

    std::string lower_title = to_lowercase(doc.title);
    std::string lower_content = to_lowercase(doc.content);

    // 标题匹配（高权重）
    for (const auto& kw : keywords) {
        if (lower_title.find(kw) != std::string::npos) {
            score += 0.5f;
        }
    }

    // 内容关键词匹配
    for (const auto& kw : keywords) {
        size_t pos = 0;
        int count = 0;
        while ((pos = lower_content.find(kw, pos)) != std::string::npos) {
            count++;
            pos += kw.length();
        }
        score += std::min(0.3f, count * 0.05f);
    }

    // 分类加成
    if (doc.category == "knowledge" && lower_query.find("理论") != std::string::npos) {
        score += 0.2f;
    }
    if (doc.category == "models" && lower_query.find("模型") != std::string::npos) {
        score += 0.2f;
    }

    return score;
}
```

### 3.3 集成到 FWITheoryAgent

```cpp
class FWITheoryAgent {
    // 新增成员
    KnowledgeBase knowledge_base_;

    // 构造函数中加载知识库
    FWITheoryAgent(...) {
        std::string resource_dir = "resources";
        if (knowledge_base_.load(resource_dir)) {
            std::cout << "知识库加载成功，文档数: "
                      << knowledge_base_.get_document_count() << std::endl;
        }
    }

    // 回答问题时检索知识库
    std::string answer_fwi_question(const std::string& query, ...) {
        // 从知识库检索相关文档
        auto relevant_docs = knowledge_base_.search(query, 3);

        // 构建知识上下文
        std::string knowledge_context;
        if (!relevant_docs.empty()) {
            knowledge_context = "\n\n## 参考资料\n";
            for (const auto& doc : relevant_docs) {
                knowledge_context += "### " + doc.title + "\n";
                knowledge_context += doc.content.substr(0, 500) + "...\n\n";
            }
        }

        // 添加到 system prompt
        system_prompt += knowledge_context;
    }
};
```

## 四、知识库内容

### 4.1 FWI 基础理论 (fwi_basics.md)

**内容**:
- FWI 历史沿革（Tarantola 1984 至今）
- 数学框架（正演、目标函数、梯度）
- 伴随状态法
- 优化算法（梯度下降、共轭梯度、L-BFGS）
- 经典参考文献

### 4.2 Cycle Skipping (cycle_skipping.md)

**内容**:
- 定义和物理机制
- 经验判据
- 典型表现
- 产生原因
- 应对策略（多尺度、包络、AWI、正则化）
- 参考文献

### 4.3 伴随状态法 (adjoint_state.md)

**内容**:
- 数学推导
- FWI 中的实现
- 计算流程
- 代码伪代码
- 计算复杂度
- 参考文献

### 4.4 多尺度反演 (multiscale_fwi.md)

**内容**:
- 理论基础
- 实现方法（频率域、时间域、Laplace-Fourier）
- 代码示例
- 工业实践
- 参考文献

### 4.5 自适应波形反演 (awi.md)

**内容**:
- AWI 目标函数
- 权重设计
- 与传统 FWI 对比
- 权重计算策略
- 参考文献

### 4.6 速度模型 Metadata (model_metadata.json)

```json
{
  "models": [
    {
      "id": "marmousi2",
      "name": "Marmousi-2 模型",
      "description": "经典 FWI 测试模型...",
      "dimensions": {"nx": 13601, "nz": 2801},
      "velocity_range": {"min": 1500, "max": 4500},
      "tags": ["2D", "acoustic", "complex", "salt"]
    }
  ]
}
```

### 4.7 数据集 Metadata (dataset_metadata.json)

```json
{
  "datasets": [
    {
      "id": "marmousi2_synthetic",
      "name": "Marmousi-2 合成数据集",
      "acquisition": {
        "type": "2D marine",
        "n_sources": 340,
        "frequency_range": [3, 40]
      }
    }
  ]
}
```

## 五、测试验证

### 5.1 启动系统

```bash
export ROUTING_MODE=agent-rag
./examples/ai_orchestrator/start_system.sh
```

### 5.2 检查知识库加载

```bash
cat examples/ai_orchestrator/logs/fwi_theory_agent.log | grep "知识库"
```

预期输出:
```
[FWITheoryAgent] 知识库加载成功，文档数: 7
```

### 5.3 测试查询

```bash
curl -X POST http://localhost:5000/ -d '{
  "jsonrpc":"2.0","id":"test","method":"message/send",
  "params":{"message":{"role":"user","contextId":"ctx",
  "parts":[{"kind":"text","text":"什么是伴随状态法"}]}}
}'
```

## 六、技术原理总结

### 6.1 RAG (Retrieval-Augmented Generation)

**原理**: 先检索相关文档，再用 LLM 生成答案。

**流程**:
```
用户问题 → 检索知识库 → 构建 prompt → LLM 生成答案
```

**优势**:
- 答案有据可查
- 减少幻觉
- 可以引用具体文献

### 6.2 关键词匹配 vs 向量匹配

| 方法 | 优点 | 缺点 |
|------|------|------|
| 关键词匹配 | 简单快速，无需额外模型 | 不够智能，无法理解语义 |
| 向量匹配 | 语义理解好 | 需要 Embedding 模型，成本高 |

**本实现**: 关键词匹配（简单快速）
**后续扩展**: 向量匹配（更智能）

### 6.3 知识库设计原则

1. **结构化**: 每个主题一个文件，便于维护
2. **元数据**: JSON 格式的 metadata，便于查询
3. **可扩展**: 支持添加新文件，无需改代码
4. **团队协作**: Markdown 格式，便于多人编辑

## 七、后续扩展

### 7.1 向量检索

```cpp
// 使用 Embedding 模型
class VectorKnowledgeBase {
    void index() {
        for (const auto& doc : documents_) {
            auto embedding = embedding_service.embed(doc.content);
            vector_index.add(doc.path, embedding);
        }
    }

    std::vector<KnowledgeDocument> search(const std::string& query, int topK) {
        auto query_embedding = embedding_service.embed(query);
        auto results = vector_index.search(query_embedding, topK);
        return results;
    }
};
```

### 7.2 MCP 工具集成

```cpp
// 通过 MCP 工具暴露知识库
// tools/list 返回:
{
  "name": "search_fwi_notes",
  "description": "搜索 FWI 知识库",
  "inputSchema": {
    "type": "object",
    "properties": {
      "query": {"type": "string"}
    }
  }
}
```

### 7.3 知识库管理

```bash
# 添加新文档
vim resources/fwi_knowledge/new_topic.md

# 重启 Agent
./examples/ai_orchestrator/stop_system.sh
./examples/ai_orchestrator/start_system.sh
```

## 八、文件结构

```
resources/
├── fwi_knowledge/
│   ├── fwi_basics.md         # FWI 基础理论
│   ├── cycle_skipping.md     # Cycle skipping
│   ├── adjoint_state.md      # 伴随状态法
│   ├── multiscale_fwi.md     # 多尺度反演
│   └── awi.md                # 自适应波形反演
├── fwi_models/
│   └── model_metadata.json   # 速度模型 metadata
├── fwi_datasets/
│   └── dataset_metadata.json # 数据集 metadata
└── fwi_notes/                # 研究笔记（待添加）

orchestrator/include/agent_rpc/orchestrator/
└── knowledge_base.h          # KnowledgeBase 类
```
