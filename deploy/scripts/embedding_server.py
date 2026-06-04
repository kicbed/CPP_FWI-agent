#!/usr/bin/env python3
"""
本地 Embedding 服务

使用 sentence-transformers 加载本地模型，提供 HTTP API。
替代 DashScope Embedding API，节省成本。

用法:
    python3 embedding_server.py --model Qwen/Qwen3-Embedding-0.6B --port 6000

API:
    POST /embed
    {
        "text": "要向量化的文本"
    }
    返回:
    {
        "embedding": [0.12, 0.34, ...]
    }

    POST /embed_batch
    {
        "texts": ["文本1", "文本2", ...]
    }
    返回:
    {
        "embeddings": [[0.12, ...], [0.34, ...], ...]
    }
"""

import argparse
import logging
from flask import Flask, request, jsonify
from sentence_transformers import SentenceTransformer

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
model = None


def load_model(model_name: str):
    """加载模型"""
    global model
    logger.info(f"加载模型: {model_name}")
    model = SentenceTransformer(model_name)
    logger.info(f"模型加载完成，维度: {model.get_sentence_embedding_dimension()}")


@app.route('/embed', methods=['POST'])
def embed():
    """单文本向量化"""
    try:
        data = request.json
        text = data.get('text', '')

        if not text:
            return jsonify({'error': 'text is required'}), 400

        embedding = model.encode(text, normalize_embeddings=True)
        return jsonify({
            'embedding': embedding.tolist(),
            'dimension': len(embedding)
        })

    except Exception as e:
        logger.error(f"Embed error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/embed_batch', methods=['POST'])
def embed_batch():
    """批量向量化"""
    try:
        data = request.json
        texts = data.get('texts', [])

        if not texts:
            return jsonify({'error': 'texts is required'}), 400

        embeddings = model.encode(texts, normalize_embeddings=True)
        return jsonify({
            'embeddings': embeddings.tolist(),
            'count': len(embeddings),
            'dimension': embeddings.shape[1]
        })

    except Exception as e:
        logger.error(f"Embed batch error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/health', methods=['GET'])
def health():
    """健康检查"""
    return jsonify({
        'status': 'ok',
        'model_loaded': model is not None,
        'dimension': model.get_sentence_embedding_dimension() if model else 0
    })


def main():
    parser = argparse.ArgumentParser(description='本地 Embedding 服务')
    parser.add_argument('--model', type=str, default='Qwen/Qwen3-Embedding-0.6B',
                        help='模型名称或路径')
    parser.add_argument('--port', type=int, default=6000,
                        help='服务端口')
    parser.add_argument('--host', type=str, default='127.0.0.1',
                        help='监听地址')

    args = parser.parse_args()

    # 加载模型
    load_model(args.model)

    # 启动服务
    logger.info(f"启动 Embedding 服务: {args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == '__main__':
    main()
