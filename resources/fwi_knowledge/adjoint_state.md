# 伴随状态法 (Adjoint-State Method)

## 一、概述

伴随状态法是一种高效计算目标函数梯度的数学方法，广泛应用于 FWI、最优控制、数据同化等领域。

**核心优势**: 通过一次正演模拟和一次伴随模拟，即可计算目标函数对所有模型参数的梯度，避免显式存储和计算巨大的 Fréchet 导数矩阵。

## 二、数学推导

### 2.1 问题设定

正演问题:
$$
\mathbf{d} = \mathcal{F}(\mathbf{m})
$$

目标函数:
$$
J(\mathbf{m}) = \frac{1}{2} \|\mathbf{d}^{obs} - \mathcal{F}(\mathbf{m})\|^2
$$

### 2.2 链式法则

$$
\nabla_{\mathbf{m}} J = -\left( \frac{\partial \mathcal{F}}{\partial \mathbf{m}} \right)^T (\mathbf{d}^{obs} - \mathbf{d}^{syn})
$$

问题: $\partial \mathcal{F} / \partial \mathbf{m}$ 是 $N_d \times N_m$ 矩阵，直接计算复杂度 $O(N_d \times N_m)$。

### 2.3 伴随方法

引入拉格朗日乘子 $\boldsymbol{\lambda}$（伴随变量），构造增广拉格朗日函数：

$$
\mathcal{L}(\mathbf{m}, \boldsymbol{\lambda}) = J(\mathbf{m}) + \boldsymbol{\lambda}^T \mathbf{R}(\mathbf{m})
$$

其中 $\mathbf{R}(\mathbf{m}) = 0$ 是正演方程的残差。

对 $\mathbf{m}$ 求导：
$$
\nabla_{\mathbf{m}} \mathcal{L} = \nabla_{\mathbf{m}} J + \left( \frac{\partial \mathbf{R}}{\partial \mathbf{m}} \right)^T \boldsymbol{\lambda}
$$

令 $\nabla_{\boldsymbol{\lambda}} \mathcal{L} = 0$ 得正演方程，令 $\nabla_{\mathbf{u}} \mathcal{L} = 0$ 得伴随方程。

### 2.4 伴随方程

$$
\left( \frac{\partial \mathbf{R}}{\partial \mathbf{u}} \right)^T \boldsymbol{\lambda} = -\frac{\partial J}{\partial \mathbf{u}}
$$

对于波动方程，伴随方程是将残差作为源、时间反向传播的波动方程。

### 2.5 梯度公式

$$
\nabla_{\mathbf{m}} J = \left( \frac{\partial \mathbf{R}}{\partial \mathbf{m}} \right)^T \boldsymbol{\lambda}
$$

## 三、FWI 中的实现

### 3.1 声波 FWI 梯度

对于声波方程：
$$
\frac{1}{v^2} \frac{\partial^2 u}{\partial t^2} - \nabla^2 u = f
$$

梯度公式：
$$
\frac{\partial J}{\partial s(\mathbf{x})} = -\int_0^T \frac{\partial^2 u}{\partial t^2}(\mathbf{x}, t) \cdot u^\dagger(\mathbf{x}, t) dt
$$

其中：
- $s(\mathbf{x}) = 1/v(\mathbf{x})$: 慢度
- $u(\mathbf{x}, t)$: 正演波场
- $u^\dagger(\mathbf{x}, t)$: 伴随波场

### 3.2 计算流程

```
Step 1: 正演模拟
    输入: 当前模型 m, 震源 f
    输出: 正演波场 u(x,t), 模拟数据 d_syn

Step 2: 计算残差
    r(t) = d_syn(t) - d_obs(t)

Step 3: 伴随模拟
    输入: 残差 r(t) 作为伴随源, 时间反向
    输出: 伴随波场 u†(x,t)

Step 4: 计算梯度
    ∇J(x) = -∫ u(x,t) · ∂²u†/∂t²(x,t) dt
```

### 3.3 代码伪代码

```python
def compute_gradient(model, sources, receivers, d_obs):
    gradient = zeros(model.shape)
    
    for s in sources:
        # Step 1: 正演
        u, d_syn = forward(model, s)
        
        # Step 2: 残差
        residual = d_syn - d_obs[s]
        
        # Step 3: 伴随
        u_adj = adjoint(model, residual, receivers)
        
        # Step 4: 梯度累加
        gradient += -sum(u * d2(u_adj)/dt2, axis=time)
    
    return gradient
```

## 四、计算复杂度

| 操作 | 复杂度 | 说明 |
|------|--------|------|
| 正演模拟 | $O(N_s \times N_x \times N_t)$ | 每个震源一次 |
| 伴随模拟 | $O(N_s \times N_x \times N_t)$ | 每个震源一次 |
| 梯度计算 | $O(N_s \times N_x \times N_t)$ | 逐点累加 |
| **总计** | $O(N_s \times N_x \times N_t)$ | 远小于直接法 |

## 五、参考文献

1. **Plessix, R. E.** (2006). A review of the adjoint-state method for computing the gradient of a functional with geophysical applications. *Geophysical Journal International*, 167(2), 495-503.

2. **Tromp, J., Tape, C., & Liu, Q.** (2005). Multiscale adjoint tomography. *Geophysical Journal International*, 160(1), 266-280.

3. **Fichtner, A., et al.** (2006). The adjoint method in seismology. *Physics of the Earth and Planetary Interiors*, 157(1-2), 86-104.
