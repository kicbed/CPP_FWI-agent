# 多尺度反演 (Multiscale FWI)

## 一、概述

多尺度反演是一种避免 cycle skipping 的经典策略，通过从低频到高频逐步引入数据，引导反演收敛到全局最小。

**核心思想**: 低频数据约束长波长结构（背景速度），高频数据恢复短波长细节（构造形态）。

## 二、理论基础

### 2.1 频率与尺度的关系

| 频率范围 | 约束尺度 | 反映信息 |
|----------|----------|----------|
| 2–5 Hz | 数百米–公里 | 背景速度、大层位 |
| 5–10 Hz | 百米级 | 主要构造、断层 |
| 10–20 Hz | 十米级 | 薄层、细节 |

### 2.2 为什么低频能避免 cycle skipping

低频波形波长长，走时差对应的时间延迟相对较小，不容易超过半周期。

**示例**:
- 3 Hz 波形：$T = 333$ ms，半周期 $T/2 = 167$ ms
- 10 Hz 波形：$T = 100$ ms，半周期 $T/2 = 50$ ms

相同 100 ms 走时误差：
- 3 Hz：未超半周期，不 cycle skip
- 10 Hz：超半周期，发生 cycle skip

## 三、实现方法

### 3.1 频率域实现

在频率域中，可以自然地选择单频率或频率组进行反演。

**流程**:
```
1. 反演 ω₁ = 2π × 2 Hz  → 更新背景速度
2. 反演 ω₂ = 2π × 4 Hz  → 细化大层位
3. 反演 ω₃ = 2π × 8 Hz  → 恢复构造
4. 反演 ω₄ = 2π × 15 Hz → 恢复细节
```

**优点**: 频率选择直观
**局限**: 需要频率域正演，内存需求大

### 3.2 时间域实现（滤波法）

在时间域中，通过对数据进行低通滤波实现多尺度。

**流程**:
```
1. 对观测数据 d_obs 和震源子波应用 0–5 Hz 低通滤波
2. 用滤波后数据反演 → 得到初始模型
3. 将上一步结果作为初始模型，用 0–10 Hz 数据反演
4. 重复，逐步增加高频
```

**优点**: 适用于时间域正演
**局限**: 滤波可能引入 artifacts

### 3.3 Laplace-Fourier 域

结合 Laplace 衰减和 Fourier 变换，同时处理低频和衰减信息。

**参考**: Shin & Cha (2008)

## 四、代码示例

```python
def multiscale_fwi(d_obs, source, model_init, freq_bands):
    """
    多尺度 FWI
    
    Parameters:
    -----------
    d_obs : ndarray
        观测数据
    source : Source
        震源
    model_init : Model
        初始模型
    freq_bands : list of tuples
        频率带列表，如 [(2,5), (5,10), (10,20)]
    """
    model = model_init.copy()
    
    for fmin, fmax in freq_bands:
        print(f"反演频带: {fmin}-{fmax} Hz")
        
        # 滤波观测数据
        d_filtered = bandpass_filter(d_obs, fmin, fmax)
        
        # 反演
        model = fwi_inversion(d_filtered, source, model)
        
        print(f"  模型更新完成")
    
    return model
```

## 五、工业实践

### 5.1 典型频率序列

| 阶段 | 频率范围 | 目标 |
|------|----------|------|
| 1 | 2–5 Hz | 背景速度 |
| 2 | 2–8 Hz | 大层位 |
| 3 | 2–12 Hz | 主要构造 |
| 4 | 2–20 Hz | 细节恢复 |

### 5.2 注意事项

1. **低频质量**: 野外数据常缺乏 < 3 Hz 有效信号，需要特殊采集或数据重建
2. **频率间隔**: 频率间隔不宜过大，否则相邻频带间模型不一致
3. **正则化**: 每个频带都需要适当的正则化

## 六、参考文献

1. **Bunks, C., et al.** (1995). Multiscale seismic waveform inversion. *Geophysics*, 60(5), 1457-1473.

2. **Sirgue, L., & Pratt, R. G.** (2004). Efficient waveform inversion and imaging: A strategy for selecting temporal frequencies. *Geophysics*, 69(1), 231-248.

3. **Shin, C., & Cha, Y. H.** (2008). Waveform inversion in the Laplace domain. *Geophysical Journal International*, 173(3), 922-931.

4. **Brossier, R., Operto, S., & Virieux, J.** (2009). Two-dimensional seismic imaging of the Valhall model from synthetic OBS data by frequency-domain acoustic full-waveform inversion. *SEG Technical Program Expanded Abstracts*.
