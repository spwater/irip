# IRIP Research Sandbox — 科学计算镜像
# Tag: research-sandbox-scipy-2026.08
#
# 包含：NumPy / Pandas / SciPy / Matplotlib / Seaborn / scikit-learn / statsmodels
#       + plotly / sympy / networkx / openpyxl / xlrd / pyarrow / h5py
#
# 构建命令：
#   docker build -t research-sandbox-scipy-2026.08 -f docker/sandbox/Dockerfile .
# 或带国内镜像加速：
#   docker build -t research-sandbox-scipy-2026.08 -f docker/sandbox/Dockerfile .

FROM python:3.12-slim

# 使用清华 PyPI 镜像加速
ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ENV PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn

# 系统依赖（matplotlib 需要字体库，scipy 需要 BLAS）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libgomp1 \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# 工作目录（在创建用户后设置权限）
WORKDIR /workspace

# 安装科学计算库（分层缓存：核心库 + 扩展库）
# 核心库
RUN pip install --no-cache-dir \
    numpy==2.2.0 \
    pandas==2.2.3 \
    scipy==1.15.0 \
    matplotlib==3.10.0 \
    seaborn==0.13.2 \
    scikit-learn==1.6.0 \
    statsmodels==0.14.4

# 扩展库
RUN pip install --no-cache-dir \
    plotly==5.24.1 \
    sympy==1.13.3 \
    networkx==3.4.2 \
    openpyxl==3.1.5 \
    xlrd==2.0.1 \
    pyarrow==18.0.0 \
    h5py==3.12.1 \
    xlsxwriter==3.2.0 \
    tabulate==0.9.0 \
    jsonschema==4.23.0

# matplotlib 中文字体配置
RUN python -c "import matplotlib; matplotlib.use('Agg'); import matplotlib.font_manager as fm; fm._load_fontmanager(try_read_cache=False)"

# 创建非 root 用户并设置工作目录权限
RUN useradd -m -s /bin/bash sandbox && \
    mkdir -p /workspace && \
    chown sandbox:sandbox /workspace

# 切换非 root 用户
USER sandbox

# 默认保持运行（由 Orchestrator 通过 docker exec 执行 Python）
CMD ["sleep", "infinity"]
