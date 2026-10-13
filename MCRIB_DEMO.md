# M-CRIB公开数据脑提取对照

这组图展示公开M-CRIB 2.0数据中P01、P02的BET与ANUBEX-SA脑提取边界，帮助检查脑外组织排除、脑缘覆盖及局部差异。重点切片初查倾向B的外轮廓更合理，但这不是完整脑区分割验证，也没有证明B在海马或全队列上更准确。

## 图像与阅读方法

橙色为A的BET边界，蓝色为B的ANUBEX-SA边界。每行同一原图、同一层面、同一显示灰度窗；前三列保留完整处理视野，第四列使用同一图像作局部放大。放大图的A为虚线、B为实线；黄色框仅表示放大范围，不是真实脑范围。

![M-CRIB P01脑提取全图与局部对照](mcrib-p01-brain-mask-ab.png)

![M-CRIB P02脑提取全图与局部对照](mcrib-p02-brain-mask-ab.png)

图中的原生axis和index属于1 mm处理网格，不是采集序列层号。方向标记与RAS毫米坐标用于定位；图像未额外重切为按解剖结构校正的正位平面。显示原始头位差异不等于配准错误。

## 实际运行条件

| 项目 | 固定条件 |
| --- | --- |
| 输入 | 公开原始全视野T2重采样为1 mm处理网格，保留发布者原文件；A/B使用同一输入与空间头信息 |
| A脑提取 | DrawEM预处理使用BET，参数 `-R -f 0.1 -m` |
| B脑提取 | ANUBEX-SA Task402，nnUNet v1，五折集成、TTA，滑窗step size 0.5；不作连通域清理或人工编辑 |
| B推理设备 | CPU、float32，4线程；未使用GPU混合精度 |
| 显示 | P01固定灰度范围8–2043，P02为11–2078；同一例各方法各层使用同一范围 |
| 后续状态 | A完整DrawEM已完成；B只完成脑提取，完整掩膜复核和人工确认后才能运行全新DrawEM |

后续DrawEM比较计划采用相同预处理、PMA和ALBERT/non-rigid-v2图谱链。这里的PNG没有显示内部脑区标签，不应称为“A/B全脑分区对照”。

## 初查判断范围

本次实际查看P01的14/106个证据页、P02的15/104页，合计144个三方向原生切片平面；这不是全体积视觉验收。两例掩膜与输入网格、毫米单位及来源SHA-256相符，二值掩膜均只有一个6连通域及一个26连通域；连通性正常也不能排除与主体相连的误分。

已查看位置中，B外轮廓总体更贴近可见脑表面，未见眼球或面部大片组织误入。A不是解剖真值，B体积较小或顶部少覆盖几层本身既不能证明改进，也不能直接判漏脑。

P01原始采集最末层仍有脑组织样结构，保留采集覆盖范围疑点，不将它记成B独有的失败。P02顶部K=95–100存在弱信号/部分容积范围，B止于K=97而A到K=102；是否仅排除脑外组织，仍需结合邻层、多方向及预定脑边界定义核对。脑脊液保留范围、脑干尾端截断和海马边界均不由本组外轮廓图验收。

本组为两例开发性展示，不提供临床准确率、QC提升百分比或统计显著性，不授予临床使用、区域验收或研究纳入资格。

## 数据署名和许可

原始数据由Bonnie Alexander及M-CRIB研究团队发布，来自[M-CRIB 2.0 neonatal brain atlas dataset](https://osf.io/4vthr/)。[研究团队官方入口](https://github.com/DevelopmentalImagingMCRI/M-CRIB_atlas)链接到该个体数据集。P01、P02是公开数据集原有编号，不是本地临床病例编号。

OSF项目于2026-10-13核对为[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)；项目与许可元数据可分别通过[OSF项目API](https://api.osf.io/v2/nodes/4vthr/)和[OSF许可API](https://api.osf.io/v2/licenses/563c1cf88c5e4a3877f9e96a/)查阅。本项目对下列两张派生PNG也采用CC BY 4.0；署名应包含原数据作者/团队、数据来源及本项目图像处理贡献。该图像许可不改变其他代码或第三方模型/图谱的许可。

| 展示图 | 公开源文件 |
| --- | --- |
| `mcrib-p01-brain-mask-ab.png` | [M-CRIB_P01_T2_raw.nii.gz](https://osf.io/4vthr/files/osfstorage/5df1d4a94bc0790009c0af21) |
| `mcrib-p02-brain-mask-ab.png` | [M-CRIB_P02_T2_raw.nii.gz](https://osf.io/4vthr/files/osfstorage/5df1d4aed039df000b60808c) |

处理变化包括1 mm重采样、固定灰度窗切片显示、A/B掩膜边界叠加、中文图注与局部放大；没有修改原始下载文件，没有人工移动边界或清理小岛。图像按原许可“按原样提供、不作保证”的条件展示。原作者及其机构不为本项目方法、结果或临床用途背书。

本仓库仅发布两张PNG，不复制原始NIfTI、人工分区、人口学表或医院病例。图像本身可能包含头部外观；重命名不能构成去标识化，本例的公开依据是原数据集的公开发布与许可，不是改名。

## 参考文献

- Alexander B, et al. A new neonatal cortical and subcortical brain atlas: the Melbourne Children's Regional Infant Brain (M-CRIB) atlas. *NeuroImage*, 2017, 147:841–851. [doi:10.1016/j.neuroimage.2016.09.068](https://doi.org/10.1016/j.neuroimage.2016.09.068).
- Alexander B, et al. Desikan-Killiany-Tourville Atlas Compatible Version of M-CRIB Neonatal Parcellated Whole Brain Atlas: The M-CRIB 2.0. *Frontiers in Neuroscience*, 2019, 13:34. [doi:10.3389/fnins.2019.00034](https://doi.org/10.3389/fnins.2019.00034).
- 本项目使用的工具及引用边界见[THIRD_PARTY.md](THIRD_PARTY.md)，脑掩膜人工与Agent复核流程见[MASK_REVIEW.md](MASK_REVIEW.md)。

## 图像校验值

```text
6722aa0ee1a76f7bf6175bd874b3c3939c56e2cbb26ca80c755aeb82a5b054f6  mcrib-p01-brain-mask-ab.png
9c0feba7ffe919a6d0eaa8810885b84bab7e527012a3bcb1be2727e013ade68c  mcrib-p02-brain-mask-ab.png
```

展示与技术初查日期：2026-10-13。
