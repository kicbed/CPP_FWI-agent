# 自适应波形反演 (Adaptive Waveform Inversion, AWI)

## 一、概述

自适应波形反演（AWI）是一种改进的 FWI 方法，通过动态加权残差来抑制 cycle skipping，同时保持 FWI 的高分辨率能力。

**核心思想**: 不是修改目标函数（如包络反演），而是在原目标函数基础上引入自适应权重，抑制不可靠的残差、增强可靠的残差。

## 二、数学框架

### 2.1 AWI 目标函数

$$
J_{AWI}(\mathbf{m}) = \frac{1}{2} \sum_{s,r} \int_0^T w_{s,r}^2(t) \left( d^{obs}_{s,r}(t) - d^{syn}_{s,r}(t; \mathbf{m}) \right)^2 dt
$$

其中 $w_{s,r}(t)$ 是自适应权重，根据数据可靠性动态调整。

### 2.2 权重设计

**基于走时可靠性**:
$$
w_{s,r}(t) = \begin{cases}
1 & \text{if } |\Delta t_{s,r}| < T/2 \\
\exp\left(-\frac{(|\Delta t_{s,r}| - T/2)^2}{2\sigma^2}\right) & \text{otherwise}
\end{cases}
$$

**基于振幅信噪比**:
$$
w_{s,r}(t) = \frac{|d^{obs}_{s,r}(t)|}{\max_t |d^{obs}_{s,r}(t)|}
$$

### 2.3 迭代更新

```
for iteration = 1, 2, ...:
    1. 正演: d_syn = F(m)
    2. 计算残差: r = d_syn - d_obs
    3. 计算权重: w = compute_weights(d_obs, d_syn)
    4. 加权残差: r_weighted = w * r
    5. 伴随模拟: 用 r_weighted 作为源
    6. 计算梯度
    7. 更新模型
```

## 三、与传统 FWI 的对比

| 特性 | 传统 FWI | AWI |
|------|----------|-----|
| 目标函数 | $L^2$ 范数 | 加权 $L^2$ 范数 |
| Cycle skipping | 敏感 | 更鲁棒 |
| 计算开销 | 基准 | 增加约 30% |
| 分辨率 | 高 | 高（保持 FWI 优势） |
| 实现复杂度 | 简单 | 中等 |

## 四、权重计算策略

### 4.1 互相关延迟法

```python
def compute_weights_cc(d_obs, d_syn, dt, max_lag_ms=50):
    """基于互相关延迟计算权重"""
    weights = ones_like(d_obs)
    
    for s in range(n_sources):
        for r in range(n_receivers):
            # 互相关
            corr = correlate(d_obs[s,r], d_syn[s,r])
            lag = argmax(corr) - len(d_syn[s,r]) + 1
            lag_ms = lag * dt * 1000
            
            # 超过阈值则降低权重
            if abs(lag_ms) > max_lag_ms:
                decay = exp(-(abs(lag_ms) - max_lag_ms)**2 / (2 * 20**2))
                weights[s,r] *= decay
    
    return weights
```

### 4.2 振幅归一化法

```python
def compute_weights_amp(d_obs):
    """基于振幅归一化计算权重"""
    # 归一化振幅
    amp = abs(d_obs)
    amp_norm = amp / amax(amp, axis=-1, keepdims=True)
    
    # 权重 = 归一化振幅
    return amp_norm
```

## 五、参考文献

1. **Warner, M., & Guasch, L.** (2016). Adaptive waveform inversion: Theory. *Geophysics*, 81(6), R429-R445.

2. **Guasch, L., et al.** (2019). Adaptive waveform inversion: Practice. *Geophysics*, 84(3), R447-R461.

3. **Warner, M., et al.** (2018). Anisotropic 3D full-waveform inversion. *Geophysics*, 83(4), R59-R80.
