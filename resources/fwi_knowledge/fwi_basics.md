# 全波形反演 (Full Waveform Inversion, FWI) 基础理论

## 一、概述

全波形反演（Full Waveform Inversion, FWI）是一种高分辨率地震成像方法，通过最小化观测数据与模拟数据之间的残差来反演地下介质参数模型。

**核心思想**: 利用地震波形的全部信息（振幅、相位、走时、波形形状），而非仅走时或振幅，来重建地下速度结构。

## 二、历史沿革

| 年份 | 里程碑 | 贡献者 |
|------|--------|--------|
| 1984 | 提出 FWI 理论框架 | Tarantola |
| 1986 | 声波 FWI | Tarantola |
| 1990 | 弹性波 FWI | Mora |
| 1999 | 频率域 FWI | Pratt & Worthington |
| 2006 | 多尺度 FWI | Bunks et al. |
| 2009 | 包络反演 | Wu et al. |
| 2013 | 自适应波形反演 AWI | Warner & Guasch |
| 2019 | 深度学习辅助 FWI | Araya-Polo et al. |

## 三、数学框架

### 3.1 正演问题

地震波传播由波动方程描述：

**声波方程**:
$$
\frac{1}{v^2(\mathbf{x})} \frac{\partial^2 u}{\partial t^2} - \nabla^2 u = f(\mathbf{x}, t)
$$

其中：
- $v(\mathbf{x})$: P 波速度
- $u(\mathbf{x}, t)$: 波场（压力或位移）
- $f(\mathbf{x}, t)$: 震源项

**正演算子**: $\mathbf{d}^{syn} = \mathcal{F}[\mathbf{m}]$

将模型参数 $\mathbf{m}$（如慢度 $s = 1/v$）映射为模拟地震记录。

### 3.2 目标函数

FWI 最小化如下最小二乘目标函数：

$$
J(\mathbf{m}) = \frac{1}{2} \sum_{s=1}^{N_s} \sum_{r=1}^{N_r} \int_0^T \left| d^{obs}_{s,r}(t) - d^{syn}_{s,r}(t; \mathbf{m}) \right|^2 dt
$$

其中：
- $\mathbf{m}$: 模型参数（如慢度 $s(\mathbf{x}) = 1/v(\mathbf{x})$）
- $d^{obs}_{s,r}(t)$: 第 $s$ 个震源、第 $r$ 个检波器的观测波形
- $d^{syn}_{s,r}(t; \mathbf{m})$: 基于当前模型 $\mathbf{m}$ 的模拟波形
- $N_s$: 震源数量
- $N_r$: 检波器数量
- $T$: 记录长度

### 3.3 梯度计算

目标函数对模型参数的梯度：

$$
\nabla_{\mathbf{m}} J = -\sum_{s=1}^{N_s} \sum_{r=1}^{N_r} \int_0^T \frac{\partial d^{syn}_{s,r}}{\partial \mathbf{m}} \left( d^{obs}_{s,r}(t) - d^{syn}_{s,r}(t) \right) dt
$$

直接计算 Fréchet 导数矩阵 $\partial d^{syn} / \partial \mathbf{m}$ 计算量巨大。

**解决方案**: 伴随状态法（Adjoint-State Method）

## 四、伴随状态法 (Adjoint-State Method)

### 4.1 核心思想

通过一次正演模拟和一次伴随模拟，高效计算梯度，避免显式存储 Fréchet 矩阵。

### 4.2 计算步骤

1. **正演模拟**: 使用当前模型 $\mathbf{m}$，计算波场 $u(\mathbf{x}, t)$
2. **计算残差**: $r_{s,r}(t) = d^{syn}_{s,r}(t) - d^{obs}_{s,r}(t)$
3. **伴随模拟**: 将残差作为伴随源，时间反向传播，得到伴随波场 $u^\dagger(\mathbf{x}, t)$
4. **计算梯度**:
   $$
   \nabla_{\mathbf{m}} J = -\sum_s \int_0^T u(\mathbf{x}, t) \frac{\partial^2 u^\dagger}{\partial t^2}(\mathbf{x}, t) dt
   $$

### 4.3 计算复杂度

| 操作 | 复杂度 |
|------|--------|
| 正演模拟 | $O(N_s \times N_x \times N_t)$ |
| 伴随模拟 | $O(N_s \times N_x \times N_t)$ |
| 梯度计算 | $O(N_s \times N_x \times N_t)$ |
| **总计** | $O(N_s \times N_x \times N_t)$ |

相比直接计算 Fréchet 矩阵的 $O(N_s \times N_r \times N_x \times N_t)$，效率提升显著。

## 五、优化算法

### 5.1 梯度下降

$$
\mathbf{m}_{k+1} = \mathbf{m}_k - \alpha_k \nabla_{\mathbf{m}} J(\mathbf{m}_k)
$$

**优点**: 简单
**缺点**: 收敛慢，对步长敏感

### 5.2 共轭梯度

$$
\mathbf{m}_{k+1} = \mathbf{m}_k + \alpha_k \mathbf{p}_k
$$

其中搜索方向 $\mathbf{p}_k$ 由共轭条件确定。

**优点**: 比梯度下降快
**缺点**: 需要存储历史信息

### 5.3 L-BFGS (Limited-memory BFGS)

准牛顿法，近似 Hessian 逆矩阵，只存储最近 $m$ 步信息。

**优点**: 收敛快，内存友好
**缺点**: 需要调参

## 六、参考文献

1. **Tarantola, A.** (1984). Inversion of seismic reflection data in the acoustic approximation. *Geophysics*, 49(8), 1259-1266.

2. **Tarantola, A.** (1986). A strategy for nonlinear elastic inversion of seismic reflection data. *Geophysics*, 51(10), 1893-1903.

3. **Pratt, R. G., & Worthington, M. H.** (1990). Inverse theory applied to multi-source cross-hole tomography. *Geophysical Prospecting*, 38(3), 287-310.

4. **Mora, P.** (1987). Nonlinear two-dimensional elastic inversion of multioffset seismic data. *Geophysics*, 52(9), 1211-1228.

5. **Virieux, J., & Operto, S.** (2009). An overview of full-waveform inversion in exploration geophysics. *Geophysics*, 74(6), WCC1-WCC26.

6. **Fichtner, A.** (2011). *Full Seismic Waveform Modelling and Inversion*. Springer.
