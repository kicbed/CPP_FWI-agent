# 全波形反演（Full-Waveform Inversion, FWI）基础

## 一、FWI 在做什么

FWI 通过波动方程生成模拟数据，并迭代调整介质模型，使选定和预处理后的模拟波形与
观测波形相符。相比只拟合人工拾取走时，它可以利用振幅、相位和波形形状；但“全波
形”不意味着所有记录都原样参与反演，实际流程通常会选择频带、时间窗、偏移距和事件。

FWI 是非线性、通常非凸的反问题。结果受初始模型、采集照明、频带、噪声、震源估计、
正演物理、参数化和正则化共同影响。目标函数下降只说明当前数据拟合改善，不单独证明
模型唯一或地质正确。

## 二、二维常密度声学示例

一种常见归一化形式是

$$
\frac{1}{v^2(\mathbf x)}\frac{\partial^2u}{\partial t^2}
-\nabla^2u=f(\mathbf x,t),
$$

其中 $v$ 是 P 波速度，$u$ 是标量波场，$f$ 是震源项。令平方慢度
$m=1/v^2$，正演算子可以写作

$$
d^{syn}=\mathcal F(m).
$$

这个模型忽略密度变化、剪切波、各向异性、衰减和三维传播等效应。用于实际数据前，
必须判断这些简化是否足以描述目标数据。

## 三、最小二乘目标函数

若残差定义为 $r=d^{syn}-d^{obs}$，常规逐采样 $L^2$ 目标为

$$
J(m)=\frac{1}{2}\sum_{s=1}^{N_s}\sum_{r=1}^{N_r}
\int_0^T
\left|d^{syn}_{s,r}(t;m)-d^{obs}_{s,r}(t)\right|^2dt.
$$

在实际实现中还要明确：道权重、采样间隔因子、归一化、静音窗、滤波以及是否对炮次
或检波器求平均。不同归一化会改变损失和梯度的数值尺度，因此不同程序或频带的 loss
不能只凭绝对值直接比较。

## 四、梯度

线性化正演算子后，模型扰动满足

$$
\delta d=\mathcal F'(m)\,\delta m,
$$

所以在相应内积下

$$
\nabla_m J=\mathcal F'(m)^*r.
$$

伴随状态法计算的是这个“Jacobian 的伴随作用于残差”，而不显式保存整个 Jacobian。
对每个震源通常进行一次正演和一次伴随传播，再把梯度累加。

梯度的具体公式取决于模型参数化。对
$A(m)u=m\,\partial_t^2u-\nabla^2u$，$m=1/v^2$ 是平方慢度；若直接优化速度 $v$，则需
通过 $dm/dv=-2/v^3$ 转换。残差和拉格朗日函数的符号约定也会改变公式表面上的负号。
详见 `adjoint_state.md`。

自动微分得到的是离散计算图对实际优化张量的导数。方向导数或 Taylor 检验可用于确认
它与所实现的离散目标函数一致。

## 五、迭代更新

抽象的一阶更新为

$$
m_{k+1}=\Pi_{\mathcal C}
\left[m_k-\alpha_k P_k\nabla J(m_k)\right],
$$

其中 $\alpha_k$ 是步长，$P_k$ 可表示预条件或优化器历史，$\Pi_{\mathcal C}$ 表示速度
边界等可行域投影。常见选择包括：

- 最速下降：实现简单，但对尺度和步长敏感；
- 非线性共轭梯度：用少量向量构造新的搜索方向；
- L-BFGS：用有限数量的模型/梯度差近似逆 Hessian，通常结合线搜索；
- Adam：在自动微分原型中方便，但超参数和逐参数尺度会影响物理解释。

不存在对所有 FWI 问题都最佳的优化器。每次迭代应检查损失、梯度和模型是否有限，
记录失败迭代，并验证模型确实更新且满足物理边界。

## 六、cycle skipping 与尺度

如果初始模拟波形与观测波形错误地对应到相邻周期，局部 $L^2$ 优化可能发生 cycle
skipping。对孤立窄带到达，“走时差约小于所选频率半周期”是常用启发式，不是宽带、
多事件数据的严格判据。低到高频的多尺度反演可以减轻风险，但不能保证全局收敛。
详见 `cycle_skipping.md` 与 `multiscale_fwi.md`。

AWI 是基于卷积匹配滤波器的另一类目标函数，不是简单的动态加权 $L^2$ 残差；详见
`awi.md`。

## 七、本项目的适用范围

当前项目验证的是二维、常密度、单参数 $V_p$、Deepwave scalar 和合成数据流程。
观测数据和反演传播均由同一数值后端生成，属于合成端到端/“逆犯罪”验证，主要验证
系统调用、梯度、优化和结果展示，不能据此宣称对实际数据具有普遍反演效果。

Marmousi 演示使用 10 m 网格和主频 8 Hz 的 Ricker 子波；这些是当前实验参数，不是
适用于所有模型的采样或频率结论。

## 八、参考文献

1. Tarantola, A. (1984). Inversion of seismic reflection data in the acoustic
   approximation. *Geophysics*, 49(8), 1259–1266.
2. Mora, P. (1987). Nonlinear two-dimensional elastic inversion of multioffset
   seismic data. *Geophysics*, 52(9), 1211–1228.
3. Bunks, C., Saleck, F. M., Zaleski, S., & Chavent, G. (1995). Multiscale
   seismic waveform inversion. *Geophysics*, 60(5), 1457–1473.
4. Pratt, R. G. (1999). Seismic waveform inversion in the frequency domain,
   Part 1: Theory and verification in a physical scale model. *Geophysics*,
   64(3), 888–901.
5. Virieux, J., & Operto, S. (2009). An overview of full-waveform inversion in
   exploration geophysics. *Geophysics*, 74(6), WCC1–WCC26.
