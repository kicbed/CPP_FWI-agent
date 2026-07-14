# 多尺度全波形反演（Multiscale FWI）

## 一、基本思想

多尺度 FWI 把反演划分为若干阶段，先拟合较平滑或运动学更简单的数据成分，再逐步
增加细节。最常见的实现是从可靠的低频开始，逐渐提高截止频率或加入更高频带，并把
上一阶段模型作为下一阶段的初始模型。

它的主要作用是降低局部优化早期面对的非线性，并减轻 cycle skipping 风险；它不能
保证收敛到全局最小值，也不能补回观测数据中不存在或信噪比不足的低频信息。

## 二、频率、波长与模型尺度

局部波长满足

$$
\lambda(\mathbf x)=\frac{v(\mathbf x)}{f}.
$$

降低频率会增大波长，并通常拓宽一个孤立到达的半周期相位容忍范围。因此低频阶段更
适合建立模型的长波长背景，较高频阶段可在数据照明允许时补充更短波长结构。

但是，模型可恢复的空间波数还取决于传播速度、散射角、透射/反射类型、孔径、震源与
检波器位置和正则化，不能仅凭频率给出固定的“米级分辨率”表格。例如同一 8 Hz 分量在
1500 m/s 和 5500 m/s 区域的波长并不相同。

## 三、半周期解释的限定

对孤立、形状相近、近似窄带且主要只有走时偏移的事件，常用启发式是

$$
|\Delta t|\lesssim\frac{1}{2f}.
$$

较低 $f$ 对同一个 $\Delta t$ 给出更长的半周期，所以错误周期配准的风险通常较低。
这只是简化诊断：宽带波形、多事件干涉、振幅/极性变化和噪声下不存在单一的严格阈值。
多尺度反演是否有效必须通过各频带数据拟合、梯度和模型更新共同验证。

## 四、常见实现

### 4.1 时间域的嵌套低通阶段

每个阶段使用逐渐升高的低通截止频率。观测和模拟数据必须采用相同的滤波器（或使用
与该频带一致的震源并保持正演/观测处理一致），不能只滤观测数据。

```python
model = model_initial

for cutoff_hz in cutoff_schedule:
    for iteration in range(iterations_per_stage):
        predicted = forward(model, source)
        observed_band = zero_phase_lowpass(observed, cutoff_hz)
        predicted_band = zero_phase_lowpass(predicted, cutoff_hz)
        loss = 0.5 * squared_l2(predicted_band - observed_band)
        model = optimizer_step(model, gradient(loss, model))
```

滤波器的相位响应、端点瞬态和有效通带都要记录。若使用因果滤波，应确保两类数据具有
一致相位响应。

### 4.2 分频带或频率域阶段

可以使用重叠频带，或在频率域中逐步选取离散频率。频率选择应考虑最低有效频率、频率
采样、模型更新幅度和相邻阶段的连续性。时间域与频率域各有不同的线性求解、内存和
并行特性，不能笼统断言某一域一定更省内存或更稳定。

### 4.3 其他“尺度”

多尺度不只指频率。实践中还可逐步增加偏移距、时间窗、事件类型，或从包络/走时类
目标过渡到逐采样波形目标。这些策略改变了数据复杂度，但每次阶段切换都可能改变
目标函数，需检查损失值是否可比较以及模型是否仍满足约束。

## 五、设计和验收建议

- 从数据中实际可用且信噪比足够的最低频率开始，而不是套用固定的 2 Hz、3 Hz 或
  5 Hz 阈值。
- 同时检查观测与模拟数据的频谱，避免反演模拟数据中没有的频率。
- 频带切换时保存每阶段的配置、损失定义和模型；跨阶段目标函数数值未必可直接比较。
- 用波形叠合或频带化相位差诊断 cycle skipping，而不是只看总损失下降。
- 结合速度边界、平滑或其他合理正则化；多尺度本身不是正则化的替代品。

## 六、与本项目的关系

当前 Marmousi 演示默认采用 10 m 网格和主频 8 Hz 的 Ricker 子波。这是一个宽带子波，
“8 Hz”不是单一频率；默认的单阶段 L2 演示也不能自动称为多尺度 FWI。只有显式配置
并记录多个频率阶段、且各阶段实际执行时，才应报告为频率递进反演。

10 m 与 8 Hz 仅适用于本次小型合成验证，不能据此推导其他模型或实际数据的普遍采样、
分辨率或起始频率建议。

## 七、参考文献

1. Bunks, C., Saleck, F. M., Zaleski, S., & Chavent, G. (1995).
   Multiscale seismic waveform inversion. *Geophysics*, 60(5), 1457–1473.
2. Sirgue, L., & Pratt, R. G. (2004). Efficient waveform inversion and imaging:
   A strategy for selecting temporal frequencies. *Geophysics*, 69(1), 231–248.
3. Virieux, J., & Operto, S. (2009). An overview of full-waveform inversion in
   exploration geophysics. *Geophysics*, 74(6), WCC1–WCC26.
