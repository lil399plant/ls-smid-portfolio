import refinitiv.data as rd
import pandas as pd
import numpy as np
from sklearn.cross_decomposition import PLSRegression
from sklearn.decomposition import PCA
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import StandardScaler
from xgboost import XGBRegressor
from datetime import datetime, timedelta
import warnings

# 屏蔽 Pandas 和底层库的降级警告，保持终端干净
warnings.simplefilter(action='ignore', category=FutureWarning)

# 1. 初始化 Refinitiv 会话
try:
    rd.open_session()
    print("Refinitiv 终端连接成功。")
except Exception as e:
    print(f"连接失败: {e}")

# 你可以把这里换成测试清单里的 CROX, NTNX, WBA 等
ticker = 'IDCC.O' 

# ==========================================
# 模块一：基本面底座 (Fundamental Engine)
# ==========================================
def get_fundamental_score(ticker):
    print("正在拉取基本面 5 因子数据...")
    fields = [
        'TR.EVtoEBITDAMean', 'TR.PtoEPSMean',      
        'TR.ROICMean', 'TR.GrossProfitMargin',     
        'TR.NetDebtToEBITDA',                      
        'TR.PriceClose'
    ]
    df = rd.get_data(universe=[ticker], fields=fields)
    
    if df.empty: return 0.5 

    try:
        pe = float(df['Price To EPS - Mean'].iloc[0])
        roic = float(df['ROIC - Mean'].iloc[0])
        debt = float(df['Net Debt To EBITDA (Daily Time Series)'].iloc[0])
        
        val_score = 1 if pe < 20 else (0 if pe < 40 else -1)
        qual_score = 1 if roic > 15 else (0 if roic > 5 else -1)
        health_score = 1 if debt < 3 else -1
        
        fund_score = (val_score * 0.25) + (qual_score * 0.25) + (health_score * 0.15)
        multiplier = max(0.1, min(1.0, (fund_score + 1) / 2))
        print(f"基本面评估完成 -> 综合得分子: {fund_score:.2f}, 仓位乘数: {multiplier:.2f}")
        return multiplier
    except:
        return 0.5

# ==========================================
# 模块二：特征工程与历史数据拉取
# ==========================================
def get_quant_features(ticker, days_back=800): # 增加回溯天数确保数据够长
    print("正在拉取量价时序数据用于训练顾问模型...")
    start_date = (datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d')
    end_date = datetime.now().strftime('%Y-%m-%d')
    
    df = rd.get_history(
        universe=ticker,
        fields=['TR.OpenPrice', 'TR.HighPrice', 'TR.LowPrice', 'TR.ClosePrice', 'TR.Volume'],
        start=start_date,
        end=end_date,
        interval='daily'
    )
    
    if df is None or df.empty:
        raise ValueError(f"未能获取到 {ticker} 的历史数据。")

    # 统一列名以防 Refinitiv 返回格式差异
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(1)

    # 11 个基础量价因子 
    df['Ret_1d'] = df['Close Price'].pct_change()
    df['Ret_5d'] = df['Close Price'].pct_change(5)
    df['Ret_20d'] = df['Close Price'].pct_change(20)
    df['Vol_20d'] = df['Ret_1d'].rolling(20).std()
    df['RSI_14'] = 100 - (100 / (1 + df['Ret_1d'].clip(lower=0).rolling(14).mean() / df['Ret_1d'].clip(upper=0).abs().rolling(14).mean()))
    df['Price_to_MA50'] = df['Close Price'] / df['Close Price'].rolling(50).mean() - 1
    
    # 目标变量 (Y)
    df['Target_T1_Open_Diff'] = df['Open Price'].shift(-1) - df['Open Price'] 
    df['Target_20d_Ret'] = df['Close Price'].shift(-20) / df['Close Price'] - 1 
    df['Target_120d_Ret'] = df['Close Price'].shift(-120) / df['Close Price'] - 1 
    
    df.dropna(inplace=True)
    
    # 核心安全检查：如果股票上市时间太短（比如次新股），由于我们 shift 了 -120，会导致全空
    if len(df) < 50:
        raise ValueError(f"【数据量不足警告】{ticker} 历史数据过短，无法完整训练 120 天周期的长期顾问模型。请尝试 CROX, WBA 等成熟标的。")
        
    return df

# ==========================================
# 模块三：四位量化顾问 (The 4 Advisors)
# ==========================================
def run_advisors(df):
    features = ['Ret_1d', 'Ret_5d', 'Ret_20d', 'Vol_20d', 'RSI_14', 'Price_to_MA50']
    X = df[features].values
    
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    
    X_latest = X_scaled[-1].reshape(1, -1)
    
    signals = {}
    
    # 顾问 1: PLS20 
    pls20 = PLSRegression(n_components=3)
    pls20.fit(X_scaled[:-20], df['Target_20d_Ret'].values[:-20])
    # 修复：使用 np.squeeze 获取纯量，彻底解决 invalid index 报错
    pred20 = float(np.squeeze(pls20.predict(X_latest)))
    signals['PLS20'] = np.clip(np.tanh(pred20 * 10), -1, 1) 
    
    # 顾问 2: PLS120 
    pls120 = PLSRegression(n_components=3)
    pls120.fit(X_scaled[:-120], df['Target_120d_Ret'].values[:-120])
    pred120 = float(np.squeeze(pls120.predict(X_latest)))
    signals['PLS120'] = np.clip(np.tanh(pred120 * 5), -1, 1)

    # 顾问 3: PCR 
    pca = PCA(n_components=3)
    X_pca = pca.fit_transform(X_scaled[:-1])
    lr = LinearRegression()
    lr.fit(X_pca, df['Target_T1_Open_Diff'].values[:-1])
    pred_pcr = float(np.squeeze(lr.predict(pca.transform(X_latest))))
    signals['PCR'] = np.clip(np.tanh(pred_pcr), -1, 1)
    
    # 顾问 4: XGBoost 
    xgb = XGBRegressor(n_estimators=50, max_depth=3, learning_rate=0.1)
    xgb.fit(X_scaled[:-1], df['Target_T1_Open_Diff'].values[:-1])
    pred_xgb = float(np.squeeze(xgb.predict(X_latest)))
    signals['XGB'] = np.clip(np.tanh(pred_xgb), -1, 1)
    
    return signals

# ==========================================
# 主执行流程
# ==========================================
print("\n" + "="*50)
print(f"Quantamental 执行引擎启动: {ticker}")
print("="*50)

try:
    fund_multiplier = get_fundamental_score(ticker)
    df_history = get_quant_features(ticker, days_back=800)
    advisor_signals = run_advisors(df_history)
    
    print("\n【四位顾问的明日信号投票 (-1 至 1)】")
    for name, sig in advisor_signals.items():
        print(f" -> {name:8}: {sig:+.3f}")
        
    weights = {'PLS20': 0.15, 'PLS120': 0.10, 'PCR': 0.40, 'XGB': 0.35}
    final_raw_signal = sum(advisor_signals[k] * weights[k] for k in weights)
    
    print("\n" + "-"*50)
    print("【系统最终交易指令 (T+1 开盘执行)】")
    print(f"综合量化信号 (Raw Signal) : {final_raw_signal:+.3f}")
    
    target_exposure = final_raw_signal * fund_multiplier
    
    if target_exposure > 0.2:
        action = "做多 (LONG)"
    elif target_exposure < -0.2:
        action = "做空 (SHORT)"
    else:
        action = "观望 (FLAT)"
        
    print(f"基本面杠杆乘数            : {fund_multiplier:.2f}x")
    print(f"最终建议仓位 (Exposure)   : {target_exposure:+.3f} ({action})")
    print("-"*50)

except Exception as e:
    print(f"执行出错: {e}")

finally:
    rd.close_session()
