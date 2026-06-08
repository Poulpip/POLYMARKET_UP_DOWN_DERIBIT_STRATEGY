import streamlit as st
import pandas as pd
import sqlite3
import datetime
from pathlib import Path

# Setup basic config
st.set_page_config(page_title="Paper Trading Dashboard", layout="wide")
st.title("📈 Polymarket Daily Edge - Paper Trades")

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

st.header("🟢 Open Paper Trades")
if not open_trades.empty:
    # Format numerical values to 2 decimals
    format_dict = {
        'entry_polymarket_price': '${:.2f}',
        'entry_model_prob': '{:.2f}',
        'size_usdc': '${:.2f}'
    }
    
    display_open = open_trades[['id', 'market_title', 'direction', 'entry_polymarket_price', 'entry_model_prob', 'size_usdc', 'created_at']]
    st.dataframe(display_open.style.format(format_dict))
else:
    st.info("No open trades currently.")

st.header("🔘 Closed Paper Trades")
if not closed_trades.empty:
    format_dict_closed = {
        'entry_polymarket_price': '${:.2f}',
        'entry_model_prob': '{:.2f}',
        'size_usdc': '${:.2f}',
        'exit_price': '${:.2f}',
        'realized_pnl': '${:.2f}'
    }
    
    display_closed = closed_trades[['id', 'market_title', 'direction', 'entry_polymarket_price', 'exit_price', 'realized_pnl', 'size_usdc', 'closed_at']]
    
    total_pnl = closed_trades['realized_pnl'].sum()
    st.metric("Total Realized PnL", f"${total_pnl:.2f}")
    
    st.dataframe(display_closed.style.format(format_dict_closed))
else:
    st.info("No closed trades yet.")

st.sidebar.title("Controls")
if st.sidebar.button("Refresh Data"):
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.markdown("**Rules implemented:**")
st.sidebar.markdown("- Check probabilities every 5m")
st.sidebar.markdown("- Target +20% Take Profit")
st.sidebar.markdown("- 1 trade per direction per active market")
