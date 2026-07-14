# 伴随状态法（Adjoint-State Method）

## 一、用途与边界

伴随状态法用于高效计算“模型参数通过状态方程影响目标函数”时的梯度。对每个震源和
一次目标函数评估，它通常需要一次正演传播和一次伴随传播，而不必显式形成完整的
Fréchet/Jacobian 矩阵。它给出的是梯度（Jacobian 的转置作用于数据残差），不是完整
Jacobian，也不会自动解决 FWI 的非凸性。

“一次正演 + 一次伴随”的说法应按震源理解；线搜索、多个频带或多次试探模型还会
增加正演次数。波场存储、检查点和重计算策略也会影响实际时间与内存成本。

## 二、统一的符号约定

令 $u$ 为波场，模型为 $m$，正演约束为

$$
A(m)u-q=0,
$$

接收算子为 $R$，模拟数据和残差定义为

$$
d^{syn}=Ru,\qquad r=d^{syn}-d^{obs},
$$

目标函数为

$$
J(m)=\frac{1}{2}\|r\|_2^2.
$$

采用下面的拉格朗日函数：

$$
\mathcal L(u,m,\lambda)
=J(u)+\langle\lambda,A(m)u-q\rangle .
$$

令对 $u$ 的变分为零，得到伴随方程

$$
A(m)^*\lambda=-R^*r,
$$

其中 $^*$ 表示与所选内积一致的伴随（实数离散问题中通常是转置）。于是模型扰动
$\delta m$ 引起的一阶目标函数变化为

$$
\delta J
=\left\langle\lambda,
\frac{\partial A}{\partial m}[\delta m]u\right\rangle .
$$

如果改用 $\mathcal L=J-\langle\lambda,A u-q\rangle$，或者把残差定义成
$d^{obs}-d^{syn}$，伴随源和梯度公式的整体符号会相应改变。只要残差、拉格朗日函数、
伴随源和更新方向使用同一套约定，符号可以等价；孤立地比较一个负号没有意义。

## 三、常密度声学示例与参数化

考虑

$$
A(m)u=m(\mathbf x)\,\partial_t^2u-\nabla^2u,
\qquad m(\mathbf x)=\frac{1}{v^2(\mathbf x)}.
$$

这里 $m$ 是**平方慢度**，不是慢度。忽略不影响说明的边界项，并沿用上一节符号约定，
平方慢度梯度可写成

$$
g_m(\mathbf x)
=\frac{\delta J}{\delta m(\mathbf x)}
=\int_0^T \lambda(\mathbf x,t)\,\partial_t^2u(\mathbf x,t)\,dt.
$$

通过时间分部积分，也可把二阶时间导数写到伴随波场上；离散实现必须与正演算子、
时间边界条件和离散内积保持一致。

若优化变量改为慢度 $s=1/v$ 或速度 $v$，必须用链式法则：

$$
g_s=2s\,g_m,
\qquad
g_v=-\frac{2}{v^3}\,g_m.
$$

因此“对慢度的梯度”和“对平方慢度的梯度”不能共用同一个公式。不同的声学方程归一
化、密度参数化、源定义或伴随场符号也会改变外观上的比例因子和负号。

## 四、概念计算流程

```text
对每个震源：
1. 解 A(m)u = q，得到正演波场和 d_syn = Ru
2. 计算残差 r = d_syn - d_obs
3. 解 A(m)* lambda = -R* r（终端条件，通常反向传播）
4. 按选定参数化关联 u 与 lambda，累加模型梯度
5. 对所有震源求和，再由优化器更新模型
```

实际代码还要处理吸收边界、离散伴随一致性、炮批次、波场检查点、梯度缩放和速度边
界。方向导数或 Taylor 检验可以验证实现的梯度是否与离散目标函数一致。

## 五、与自动微分和本项目的关系

反向模式自动微分在计算图层面执行与离散伴随密切相关的运算。当前项目使用 Deepwave
和 PyTorch 对速度张量直接求导，因此最终得到的是所实现离散算子对速度 $v$ 的梯度；
不应把它不加转换地称作慢度或平方慢度梯度。

当前 MVP 是二维、常密度、单参数 $V_p$ 的合成验证。这里的公式不表示项目已经支持
密度、弹性参数、三维或实际数据物理。

## 六、参考文献

1. Plessix, R. E. (2006). A review of the adjoint-state method for computing
   the gradient of a functional with geophysical applications. *Geophysical
   Journal International*, 167(2), 495–503.
2. Tromp, J., Tape, C., & Liu, Q. (2005). Seismic tomography, adjoint methods,
   time reversal and banana-doughnut kernels. *Geophysical Journal
   International*, 160(1), 195–216.
3. Fichtner, A. (2011). *Full Seismic Waveform Modelling and Inversion*.
   Springer.
