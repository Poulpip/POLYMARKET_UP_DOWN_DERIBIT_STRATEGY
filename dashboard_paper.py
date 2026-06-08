import streamlit as st
import pandas as pd
import sqlite3
from pathlib import Path

# Setup basic config
st.set_page_config(
    page_title="Polymarket Edge - Paper Trading",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom premium styling
st.markdown("""
<style>
    /* Premium fonts and background */
    @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&family=Inter:wght@300;400;600;700&display=swap');
    
    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }
    
    .main-header {
        font-family: 'Outfit', sans-serif;
        font-weight: 800;
        background: linear-gradient(90deg, #3B82F6 0%, #8B5CF6 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 2rem;
    }
    
    /* Card design */
    .metric-card {
        background: rgba(255, 255, 255, 0.05);
        backdrop-filter: blur(10px);
        border: 1px solid rgba(255, 255, 255, 0.1);
        border-radius: 12px;
        padding: 1.5rem;
        box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.2);
    }
    
    /* Sidebar styling */
    .sidebar .sidebar-content {
        background-color: #0F172A;
    }
</style>
""", unsafe_allow_html=True)

st.markdown('<h1 class="main-header">⚡ Polymarket Daily Edge — Live Paper Trader</h1>', unsafe_allow_html=True)

DB_PATH = "paper_trades.db"

def get_connection():
    return sqlite3.connect(DB_PATH)

def fetch_data():
    if not Path(DB_PATH).exists():
        return pd.DataFrame(), pd.DataFrame()
        
    conn = get_connection()
    df = pd.read_sql_query("SELECT * FROM paper_trades ORDER BY created_at DESC", conn)
    conn.close()
    
    if df.empty:
        return df, df
        
    open_df = df[df['status'] == 'OPEN'].copy()
    closed_df = df[df['status'] == 'CLOSED'].copy()
    return open_df, closed_df

open_trades, closed_trades = fetch_data()

# Render Metrics
col1, col2, col3 = st.columns(3)

with col1:
    open_count = len(open_trades) if not open_trades.empty else 0
    st.metric("Active Open Positions", f"{open_count}")

with col2:
    if not closed_trades.empty:
        total_pnl = closed_trades['realized_pnl'].sum()
        win_count = len(closed_trades[closed_trades['realized_pnl'] > 0])
        loss_count = len(closed_trades[closed_trades['realized_pnl'] <= 0])
        win_rate = (win_count / len(closed_trades)) * 100.0
    else:
        total_pnl = 0.0
        win_rate = 0.0
    st.metric("Total Realized PnL", f"${total_pnl:.2f}")

with col3:
    st.metric("Win Rate (Closed)", f"{win_rate:.2f}%")

st.markdown("---")

st.header("🟢 Open Paper Trades")
if not open_trades.empty:
    # Format numerical values to exactly 2 decimal places per user rules
    format_dict = {
        'entry_polymarket_price': '${:.2f}',
        'entry_model_prob': '{:.2f}',
        'size_usdc': '${:.2f}',
        'peak_price': '${:.2f}',
        'barrier': '${:.2f}'
    }
    
    # Fill NaN peak prices
    if 'peak_price' in open_trades.columns:
        open_trades['peak_price'] = open_trades['peak_price'].fillna(open_trades['entry_polymarket_price'])
    else:
        open_trades['peak_price'] = open_trades['entry_polymarket_price']
        
    if 'barrier' not in open_trades.columns:
        open_trades['barrier'] = 0.0
        
    display_open = open_trades[[
        'id', 'market_title', 'direction', 'barrier', 'entry_polymarket_price', 
        'peak_price', 'entry_model_prob', 'size_usdc', 'expiry_timestamp', 'created_at'
    ]].copy()
    
    st.dataframe(display_open.style.format(format_dict), use_container_width=True)
else:
    st.info("No open trades currently.")

st.header("🔘 Closed Trade History")
if not closed_trades.empty:
    format_dict_closed = {
        'entry_polymarket_price': '${:.2f}',
        'entry_model_prob': '{:.2f}',
        'size_usdc': '${:.2f}',
        'exit_price': '${:.2f}',
        'realized_pnl': '${:.2f}',
        'barrier': '${:.2f}'
    }
    
    if 'exit_reason' not in closed_trades.columns:
        closed_trades['exit_reason'] = 'N/A'
    if 'barrier' not in closed_trades.columns:
        closed_trades['barrier'] = 0.0
        
    display_closed = closed_trades[[
        'id', 'market_title', 'direction', 'barrier', 'entry_polymarket_price', 
        'exit_price', 'exit_reason', 'realized_pnl', 'size_usdc', 'closed_at'
    ]].copy()
    
    st.dataframe(display_closed.style.format(format_dict_closed), use_container_width=True)
else:
    st.info("No closed trades yet.")

st.sidebar.title("Controls")
if st.sidebar.button("Refresh Data"):
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.markdown("**Strategy Parameters:**")
st.sidebar.markdown(f"- **Alpha UP:** {2.00:.2f}")
st.sidebar.markdown(f"- **Alpha DOWN:** {1.00:.2f}")
st.sidebar.markdown(f"- **Floor UP:** {0.65:.2f}")
st.sidebar.markdown(f"- **Floor DOWN:** {0.55:.2f}")
st.sidebar.markdown(f"- **Take Profit:** {30.00:.2f}%")
st.sidebar.markdown(f"- **Trail Activation:** {20.00:.2f}%")
st.sidebar.markdown(f"- **Trail Distance:** {15.00:.2f}pp")
st.sidebar.markdown(f"- **Concurrent Positions:** {True}")
