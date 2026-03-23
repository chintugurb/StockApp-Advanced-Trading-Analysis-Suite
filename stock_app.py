import sys
import os
import json
from datetime import datetime, timedelta
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QLabel, QLineEdit, QPushButton, QTextEdit, QMessageBox,
                             QTabWidget, QTableWidget, QTableWidgetItem, QComboBox,
                             QDateEdit, QFileDialog, QGridLayout, QRadioButton, QGroupBox,
                             QSpinBox, QDoubleSpinBox, QTimeEdit, QCheckBox)
from PyQt6.QtCore import Qt, QDate, QThread, pyqtSignal, QTime
from PyQt6.QtGui import QFont, QPalette, QColor
import pandas as pd
import time
import re
import requests
from bs4 import BeautifulSoup

# Assuming kite_trade2.py is your Kite library
from kite_trade2 import KiteApp
# Import for WebSocket Ticker
from kiteconnect import KiteTicker




# --- [Existing Worker Classes: DownloadWorker, etc. are assumed here] ---
# Worker for Historical Data Download (unchanged)
class DownloadWorker(QThread):
    update_status = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, kite, start_date, end_date, timeframe, output_dir, excel_path):
        super().__init__()
        self.kite = kite
        self.start_date = start_date
        self.end_date = end_date
        self.timeframe = timeframe
        self.output_dir = output_dir
        self.excel_path = excel_path
        self._stop = False

    def run(self):
        try:
            token_df = pd.read_excel(self.excel_path)
            for i in range(len(token_df)):
                if self._stop:
                    self.update_status.emit("Download stopped by user.")
                    break

                token = token_df.loc[i, 'TOKEN']
                symbol = token_df.loc[i, 'SYMBOL']
                self.update_status.emit(f"Fetching data for {symbol} (Token: {token})...")

                from_date = self.start_date
                final_df = pd.DataFrame(columns=['date', 'open', 'high', 'low', 'close', 'volume', 'oi'])

                while from_date < self.end_date and not self._stop:
                    to_date = min(from_date + timedelta(days=30), self.end_date)
                    data = self.kite.historical_data(
                        instrument_token=token,
                        from_date=from_date.strftime('%Y-%m-%d'),
                        to_date=to_date.strftime('%Y-%m-%d'),
                        interval=self.timeframe,
                        continuous=False,
                        oi=True
                    )
                    if data:
                        df = pd.DataFrame(data)
                        final_df = pd.concat([final_df, df], ignore_index=True)
                    from_date = to_date + timedelta(days=1)
                    time.sleep(0.7)  # Wait 200ms between requests to stay under rate limits

                if self._stop:
                    self.update_status.emit(f"Stopped processing {symbol}.")
                    break

                final_df['date'] = pd.to_datetime(final_df['date'], errors='coerce')
                final_df['date_only'] = final_df['date'].dt.date
                final_df['time'] = final_df['date'].dt.strftime('%H:%M')
                final_df['ticker'] = symbol
                final_df.drop('date', axis=1, inplace=True)
                final_df = final_df[['ticker', 'date_only', 'open', 'high', 'low', 'close', 'volume', 'oi', 'time']]

                filename = f"{symbol}.txt"
                final_df.to_csv(os.path.join(self.output_dir, filename), header=False, index=False)
                self.update_status.emit(f"Saved data for {symbol} to {filename}")

            if not self._stop:
                self.update_status.emit("Historical data download completed!")
        except Exception as e:
            self.update_status.emit(f"Error during download: {str(e)}")

        self.finished.emit()

    def stop(self):
        self._stop = True

# --- MODIFIED StopLossWorker ---
class StopLossWorker(QThread):
    update_status = pyqtSignal(str)
    data_ready = pyqtSignal(list)
    finished = pyqtSignal()

    def __init__(self, kite, stoploss_strategy, stoploss_percentage, data_source):
        super().__init__()
        self.kite = kite
        self.stoploss_strategy = stoploss_strategy
        self.stoploss_percentage = stoploss_percentage
        self.data_source = data_source
        self._stop = False

    def calculate_technical_indicators(self, instrument_token, min_days=5):
        from_datetime = datetime.now() - timedelta(days=14)
        to_datetime = datetime.now()
        try:
            historical_data = self.kite.historical_data(
                instrument_token=instrument_token,
                from_date=from_datetime.strftime('%Y-%m-%d'),
                to_date=to_datetime.strftime('%Y-%m-%d'),
                interval="day"
            )
            if not historical_data or not isinstance(historical_data, list):
                self.update_status.emit(f"No valid historical data for token {instrument_token}: {historical_data}")
                return None, None, None, None
            df = pd.DataFrame(historical_data)
            if len(df) < min_days:
                self.update_status.emit(f"Not enough data ({len(df)} days) for token {instrument_token}")
                return None, None, None, None
            window = min(20, len(df))
            ma_20_day = df['close'].rolling(window=window).mean().iloc[-1].round(1)
            highest_high = df['high'].max().round(1)
            lowest_low = df['low'].min().round(1)
            lower_4_percent = (highest_high * 0.96).round(1)
            return ma_20_day, highest_high, lowest_low, lower_4_percent
        except Exception as e:
            self.update_status.emit(f"Error fetching historical data for token {instrument_token}: {str(e)}")
            return None, None, None, None

    def process_data(self, data, data_type):
        if not isinstance(data, list):
            self.update_status.emit(f"Invalid {data_type} data: Expected list, got {type(data)} - {data}")
            return []
        processed_data = []
        for item in data:
            if not isinstance(item, dict):
                self.update_status.emit(f"Invalid {data_type} item: Expected dict, got {type(item)} - {item}")
                continue
            if item.get('quantity', 0) <= 0:
                self.update_status.emit(f"Skipping {item.get('tradingsymbol', 'Unknown')} due to zero/negative quantity")
                continue
            indicators = self.calculate_technical_indicators(item['instrument_token'])
            if any(ind is None for ind in indicators):
                continue
            ma_20_day, highest_high, lowest_low, lower_4_percent = indicators
            processed_data.append({
                'tradingsymbol': item.get('tradingsymbol', ''),
                'exchange': item.get('exchange', ''),
                'instrument_token': item.get('instrument_token', ''),
                'product': item.get('product', ''),
                'quantity': item.get('quantity', 0),
                'average_price': item.get('average_price', 0.0),
                'last_price': float(item.get('last_price', 0)) if item.get('last_price', 0) else 0.0,
                'pnl': item.get('pnl', 0.0),
                'ma_20_day': ma_20_day,
                'highest_high': highest_high,
                'lowest_low': lowest_low,
                'lower_4_percent': lower_4_percent
            })
        self.update_status.emit(f"Processed {len(processed_data)} {data_type} items")
        return processed_data

    def filter_gtt_data(self, gtt_data):
        if not isinstance(gtt_data, list):
            self.update_status.emit(f"Invalid GTT data: Expected list, got {type(gtt_data)} - {gtt_data}")
            return []
        filtered = []
        for gtt in gtt_data:
            if not isinstance(gtt, dict):
                self.update_status.emit(f"Invalid GTT item: Expected dict, got {type(gtt)} - {gtt}")
                continue
            filtered.append({
                'id': gtt.get('id', ''),
                'type': gtt.get('type', ''),
                'created_at': gtt.get('created_at', ''),
                'updated_at': gtt.get('updated_at', ''),
                'expires_at': gtt.get('expires_at', ''),
                'status': gtt.get('status', ''),
                'c_exchange': gtt.get('condition', {}).get('exchange', ''),
                'c_last_price': gtt.get('condition', {}).get('last_price', ''),
                'c_tradingsymbol': gtt.get('condition', {}).get('tradingsymbol', ''),
                'c_trigger_values': gtt.get('condition', {}).get('trigger_values', ''),
                'o_exchange': gtt.get('orders', [{}])[0].get('exchange', ''),
                'o_tradingsymbol': gtt.get('orders', [{}])[0].get('tradingsymbol', ''),
                'o_product': gtt.get('orders', [{}])[0].get('product', ''),
                'o_order_type': gtt.get('orders', [{}])[0].get('order_type', ''),
                'o_transaction_type': gtt.get('orders', [{}])[0].get('transaction_type', ''),
                'o_quantity': gtt.get('orders', [{}])[0].get('quantity', ''),
                'o_price': gtt.get('orders', [{}])[0].get('price', '')
            })
        self.update_status.emit(f"Filtered {len(filtered)} GTT orders")
        return filtered

    def calculate_stop_loss_price(self, last_price, average_price, highest_high, lowest_low):
        if self.stoploss_strategy == "Generic Stoploss":
            if last_price >= highest_high * 0.95 and last_price > average_price:
                return round(highest_high * 0.95)
            else:
                return round(lowest_low * 0.98)
        elif self.stoploss_strategy == "Fixed % Stoploss":
            # Percentage is negative, so adding it reduces the price
            return round(average_price * (1 + self.stoploss_percentage / 100.0))
        elif self.stoploss_strategy == "Trailing % Stoploss":
            # Percentage is negative, so adding it reduces the price
            return round(last_price * (1 + self.stoploss_percentage / 100.0))
        else: # Fallback to generic
            self.update_status.emit(f"Unknown stop-loss strategy: {self.stoploss_strategy}. Using Generic.")
            if last_price >= highest_high * 0.95 and last_price > average_price:
                return round(highest_high * 0.95)
            else:
                return round(lowest_low * 0.98)

    def run(self):
        try:
            self.update_status.emit(f"Starting stop-loss analysis for '{self.data_source}' with strategy: {self.stoploss_strategy}")
            self.update_status.emit("Fetching data...")

            holdings_data = []
            positions_data_net = []

            if self.data_source in ["Holdings", "Both"]:
                holdings_data = self.kite.holdings()
                self.update_status.emit(f"Holdings type: {type(holdings_data)}, Content: {str(holdings_data)[:100] if isinstance(holdings_data, list) else holdings_data}")

            if self.data_source in ["Positions", "Both"]:
                positions_data = self.kite.positions()
                positions_data_net = positions_data.get('net', [])
                self.update_status.emit(f"Positions type: {type(positions_data)}, Content: {str(positions_data)[:100] if isinstance(positions_data, dict) else positions_data}")

            gtt_data = self.kite.get_gtts()
            self.update_status.emit(f"GTTs type: {type(gtt_data)}, Content: {str(gtt_data)[:100] if isinstance(gtt_data, list) else gtt_data}")

            holdings_with_indicators = self.process_data(holdings_data, "holdings")
            positions_with_indicators = self.process_data(positions_data_net, "positions")
            filtered_gtt = self.filter_gtt_data(gtt_data)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            excel_filename = f"HoldingsAndGTTs_{timestamp}.xlsx"
            dataframes = {
                'Holdings': pd.DataFrame(holdings_with_indicators),
                'Positions': pd.DataFrame(positions_with_indicators),
                'GTTs': pd.DataFrame(filtered_gtt)
            }
            with pd.ExcelWriter(excel_filename, engine='xlsxwriter', mode='w') as writer:
                for sheet_name, df in dataframes.items():
                    if not df.empty:
                        df.to_excel(writer, sheet_name=sheet_name, index=False)
            self.update_status.emit(f"Data saved to {excel_filename}")

            # Combine dataframes based on user selection
            dfs_to_combine = []
            if self.data_source in ["Holdings", "Both"] and not dataframes['Holdings'].empty:
                dfs_to_combine.append(dataframes['Holdings'])
            if self.data_source in ["Positions", "Both"] and not dataframes['Positions'].empty:
                dfs_to_combine.append(dataframes['Positions'])

            if not dfs_to_combine:
                self.update_status.emit("No assets found in the selected source (Holdings/Positions). Aborting.")
                self.finished.emit()
                return

            combined_df = pd.concat(dfs_to_combine, ignore_index=True)
            stoploss_data = []
            gtt_df = pd.DataFrame(filtered_gtt)

            self.update_status.emit("Deleting existing SELL GTT orders...")
            if not gtt_df.empty:
                for _, row in gtt_df[gtt_df['o_transaction_type'] == "SELL"].iterrows():
                    if self._stop:
                        self.update_status.emit("Stopped by user.")
                        break
                    try:
                        response = self.kite.delete_gtt(trigger_id=row['id'])
                        self.update_status.emit(f"Deleted GTT order ID {row['id']} for {row['c_tradingsymbol']} - Response: {response}")
                    except Exception as e:
                        self.update_status.emit(f"Error deleting GTT order {row['id']}: {str(e)}")

            self.update_status.emit("Placing stop-loss GTT orders...")
            for _, row in combined_df.iterrows():
                if self._stop:
                    self.update_status.emit("Stopped by user.")
                    break
                exchange = row['exchange']
                tradingsymbol = row['tradingsymbol']
                quantity = int(row['quantity'])
                highest_high = row['highest_high']
                average_price = row['average_price']
                last_price = float(row['last_price']) if row['last_price'] else 0.0
                lowest_low = row['lowest_low']

                stop_loss_price = self.calculate_stop_loss_price(last_price, average_price, highest_high, lowest_low)

                stoploss_data.append({
                    'tradingsymbol': tradingsymbol,
                    'last_price': last_price,
                    'average_price': average_price,
                    'highest_high': highest_high,
                    'stop_loss_price': stop_loss_price,
                    'lower_4_percent': highest_high * 0.96,
                    'avg_val*0.98': average_price * 0.98,
                    'avg_val*0.96': average_price * 0.96,
                    'lowest_low': lowest_low
                })

                try:
                    order_single = [{
                        "exchange": exchange,
                        "tradingsymbol": tradingsymbol,
                        "transaction_type": self.kite.TRANSACTION_TYPE_SELL,
                        "quantity": quantity,
                        "order_type": "LIMIT",
                        "product": "CNC",
                        "price": float(round(stop_loss_price)),
                    }]
                    trigger_values = [float(round(stop_loss_price))]
                    last_price_val = float(round(last_price))
                    self.update_status.emit(f"Placing GTT for {tradingsymbol}: Trigger={trigger_values}, Last Price={last_price_val}")
                    single_gtt = self.kite.place_gtt(
                        trigger_type=self.kite.GTT_TYPE_SINGLE,
                        tradingsymbol=tradingsymbol,
                        exchange=exchange,
                        trigger_values=trigger_values,
                        last_price=last_price_val,
                        orders=order_single
                    )
                    self.update_status.emit(f"GTT placed for {tradingsymbol}: Trigger={stop_loss_price}, Response={single_gtt}")
                    time.sleep(2)
                except Exception as e:
                    self.update_status.emit(f"Error placing GTT order for {tradingsymbol}: {str(e)}")

            if stoploss_data:
                stoploss_df = pd.DataFrame(stoploss_data)
                stoploss_filename = f"Stoplossreport_{timestamp}.xlsx"
                stoploss_df.to_excel(stoploss_filename, sheet_name='Stoploss', index=False)
                self.update_status.emit(f"Stop-loss data saved to {stoploss_filename}")
                self.data_ready.emit(stoploss_data)

            if not self._stop:
                self.update_status.emit("Stop-loss processing completed!")
        except Exception as e:
            self.update_status.emit(f"Error in stop-loss processing: {str(e)}")

        self.finished.emit()

    def stop(self):
        self._stop = True

# --- [Other worker classes: DeleteGTTWorker, TargetGTTWorker, etc., remain unchanged] ---
class DeleteGTTWorker(QThread):
    update_status = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, kite):
        super().__init__()
        self.kite = kite
        self._stop = False

    def filter_gtt_data(self, gtt_data):
        if not isinstance(gtt_data, list):
            self.update_status.emit(f"Invalid GTT data: Expected list, got {type(gtt_data)} - {gtt_data}")
            return []
        filtered = []
        for gtt in gtt_data:
            if not isinstance(gtt, dict):
                self.update_status.emit(f"Invalid GTT item: Expected dict, got {type(gtt)} - {gtt}")
                continue
            filtered.append({
                'id': gtt.get('id', ''),
                'o_transaction_type': gtt.get('orders', [{}])[0].get('transaction_type', ''),
                'c_tradingsymbol': gtt.get('condition', {}).get('tradingsymbol', '')
            })
        self.update_status.emit(f"Filtered {len(filtered)} GTT orders")
        return filtered

    def run(self):
        try:
            self.update_status.emit("Fetching existing GTT orders...")
            gtt_data = self.kite.get_gtts()
            gtt_df = pd.DataFrame(self.filter_gtt_data(gtt_data))

            if gtt_df.empty:
                self.update_status.emit("No GTT orders found to delete.")
            else:
                self.update_status.emit("Deleting existing SELL GTT orders...")
                sell_gtts = gtt_df[gtt_df['o_transaction_type'] == "SELL"]
                for _, row in sell_gtts.iterrows():
                    if self._stop:
                        self.update_status.emit("Deletion stopped by user.")
                        break
                    try:
                        response = self.kite.delete_gtt(trigger_id=row['id'])
                        self.update_status.emit(f"Deleted GTT order ID {row['id']} for {row['c_tradingsymbol']} - Response: {response}")
                        time.sleep(1)
                    except Exception as e:
                        self.update_status.emit(f"Error deleting GTT order {row['id']}: {str(e)}")

                if not self._stop:
                    self.update_status.emit("All SELL GTT orders deleted successfully!")

        except Exception as e:
            self.update_status.emit(f"Error in GTT deletion: {str(e)}")

        self.finished.emit()

    def stop(self):
        self._stop = True

class TargetGTTWorker(QThread):
    update_status = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, kite, excel_path, days, strategy, percentage, quantity_strategy, quantity_amount):
        super().__init__()
        self.kite = kite
        self.excel_path = excel_path
        self.days = days
        self.strategy = strategy
        self.percentage = percentage
        self.quantity_strategy = quantity_strategy
        self.quantity_amount = quantity_amount
        self._stop = False

    def run(self):
        try:
            # Fetch Instrument List
            self.update_status.emit("Fetching NSE instrument list...")
            try:
                nse_instruments = self.kite.instruments("NSE")
                instrument_map = {
                    instrument['tradingsymbol']: instrument['instrument_token']
                    for instrument in nse_instruments
                }
                self.update_status.emit("Instrument list fetched successfully.")
            except Exception as e:
                self.update_status.emit(f"Error fetching instrument list: {str(e)}")
                return

            # Load Stock List
            try:
                stocks_df = pd.read_excel(self.excel_path)
                if 'SYMBOL' not in stocks_df.columns:
                    self.update_status.emit("Error: Excel file must contain 'SYMBOL' column.")
                    return
            except FileNotFoundError:
                self.update_status.emit("Error: Excel file not found. Please upload a valid file.")
                return
            except Exception as e:
                self.update_status.emit(f"Error reading Excel file: {str(e)}")
                return

            # Date Calculation
            to_date = datetime.today().date()
            from_date = to_date - timedelta(days=self.days)

            # Process Each Stock
            for index, row in stocks_df.iterrows():
                if self._stop:
                    self.update_status.emit("GTT order placement stopped by user.")
                    break

                symbol = row['SYMBOL']
                token = instrument_map.get(symbol)

                if not token:
                    self.update_status.emit(f"Token not found for {symbol}. Skipping...")
                    continue

                self.update_status.emit(f"Processing {symbol} (Token: {token})...")

                try:
                    # Fetch Historical Data
                    historical_data = self.kite.historical_data(
                        instrument_token=token,
                        from_date=from_date.strftime("%Y-%m-%d"),
                        to_date=to_date.strftime("%Y-%m-%d"),
                        interval="day"
                    )

                    if not historical_data:
                        self.update_status.emit(f"No historical data found for {symbol}. Skipping...")
                        continue

                    last_price = historical_data[-1]['close']

                    # Calculate Trigger Price based on Strategy
                    if self.strategy == "Max Past Days":
                        high_max = max(max(candle['open'], candle['close']) for candle in historical_data)
                        self.update_status.emit(f"{self.days}-Day High for {symbol}: {high_max}")
                        if high_max>1.003*last_price:
                            trigger_price = round(high_max)
                        else: trigger_price = round(1.004*last_price)

                    else:
                        self.update_status.emit(f"Last Price for {symbol}: {last_price}")
                        trigger_price = round(last_price * (1 + self.percentage / 100))

                    # Calculate Quantity
                    quantity = 1
                    if self.quantity_strategy == "Amount":
                        if last_price > 0:
                            calculated_quantity = int(self.quantity_amount / last_price)
                            quantity = max(1, calculated_quantity)
                            self.update_status.emit(f"For {symbol}, amount {self.quantity_amount} / last price {last_price} -> quantity {quantity}")
                        else:
                            self.update_status.emit(f"Warning: Last price for {symbol} is 0. Defaulting quantity to 1.")
                            quantity = 1

                    # Place GTT Order
                    order = [{
                        "exchange": "NSE",
                        "tradingsymbol": symbol,
                        "transaction_type": self.kite.TRANSACTION_TYPE_BUY,
                        "quantity": quantity,
                        "order_type": "LIMIT",
                        "product": "CNC",
                        "price": trigger_price,
                    }]

                    gtt_response = self.kite.place_gtt(
                        trigger_type=self.kite.GTT_TYPE_SINGLE,
                        tradingsymbol=symbol,
                        exchange="NSE",
                        trigger_values=[trigger_price],
                        last_price=last_price,
                        orders=order
                    )

                    self.update_status.emit(f"GTT order placed for {symbol}. Trigger ID: {gtt_response.get('trigger_id')}")
                    time.sleep(1)

                except Exception as e:
                    self.update_status.emit(f"Error processing {symbol}: {str(e)}")

            if not self._stop:
                self.update_status.emit("GTT order placement completed!")
        except Exception as e:
            self.update_status.emit(f"Unexpected error in GTT placement: {str(e)}")

        self.finished.emit()

    def stop(self):
        self._stop = True

class DeleteAllGTTsWorker(QThread):
    update_status = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, kite):
        super().__init__()
        self.kite = kite
        self._stop = False

    def run(self):
        try:
            self.update_status.emit("Fetching all existing GTT orders...")
            gtt_orders = self.kite.get_gtts()

            if not gtt_orders:
                self.update_status.emit("No GTT orders found to delete.")
                self.finished.emit()
                return

            self.update_status.emit(f"Found {len(gtt_orders)} GTTs. Starting deletion...")

            for order in gtt_orders:
                if self._stop:
                    self.update_status.emit("Deletion stopped by user.")
                    break

                trigger_id = order.get('id')
                symbol = order.get('condition', {}).get('tradingsymbol', 'Unknown Symbol')

                if trigger_id:
                    try:
                        response = self.kite.delete_gtt(trigger_id=trigger_id)
                        self.update_status.emit(f"Deleted GTT for {symbol} (ID: {trigger_id}) - Response: {response}")
                        time.sleep(0.5)  # To avoid rate limiting
                    except Exception as e:
                        self.update_status.emit(f"Error deleting GTT ID {trigger_id}: {str(e)}")
                else:
                    self.update_status.emit("Could not find trigger ID in order data. Skipping.")

            if not self._stop:
                self.update_status.emit("All GTT orders have been processed.")

        except Exception as e:
            self.update_status.emit(f"An error occurred during GTT deletion: {str(e)}")

        self.finished.emit()

    def stop(self):
        self._stop = True

class FundamentalScraperWorker(QThread):
    update_status = pyqtSignal(str)
    data_ready = pyqtSignal(object) # Can emit a pandas DataFrame
    finished = pyqtSignal()

    def __init__(self, input_path, input_sheet, output_path, output_sheet):
        super().__init__()
        self.input_path = input_path
        self.input_sheet = input_sheet
        self.output_path = output_path
        self.output_sheet = output_sheet
        self._stop = False

    def stop(self):
        self._stop = True
        self.update_status.emit(">>> STOP request received. Finishing current stock and then stopping...")

    def fetch_with_retry(self, url, headers, retries=3, timeout=10):
        for i in range(retries):
            if self._stop: return None
            try:
                response = requests.get(url, headers=headers, timeout=timeout)
                if response.status_code == 200:
                    return response
                else:
                    self.update_status.emit(f"    > Request failed with status code {response.status_code}")
                    time.sleep(2)
            except requests.exceptions.RequestException as e:
                self.update_status.emit(f"    > Request failed: {e}. Retrying {i+1}/{retries}...")
                time.sleep(5)
        return None

    def _to_float(self, value_str):
        if not value_str or not isinstance(value_str, str):
            return 0.0
        try:
            # Remove any characters that are not digits, a decimal point, or a minus sign
            cleaned_str = re.sub(r'[^\d.-]', '', str(value_str))
            return float(cleaned_str) if cleaned_str and cleaned_str != '-' else 0.0
        except (ValueError, TypeError):
            return 0.0

    def calculate_fundamental_score(self, financial_data, growth_data):
        weights = {
            'Dividend Yield (%)': 0.05, 'ROCE (%)': 0.10, 'ROE (%)': 0.10,
            'Compounded Sales Growth 10 years (%)': 0.08, 'Compounded Sales Growth 5 years (%)': 0.07,
            'Compounded Sales Growth 3 years (%)': 0.05, 'Compounded Sales Growth TTM (%)': 0.05,
            'Compounded Profit Growth 10 years (%)': 0.08, 'Compounded Profit Growth 5 years (%)': 0.07,
            'Compounded Profit Growth 3 years (%)': 0.05, 'Compounded Profit Growth TTM (%)': 0.05,
            'Stock Price CAGR 10 years (%)': 0.07, 'Stock Price CAGR 5 years (%)': 0.05,
            'Stock Price CAGR 3 years (%)': 0.05, 'Stock Price CAGR 1 Year (%)': 0.05,
            'Return on Equity 10 years (%)': 0.05, 'Return on Equity 5 years (%)': 0.05,
            'Return on Equity 3 years (%)': 0.05, 'Return on Equity Last year (%)': 0.05
        }
        combined_data = {**financial_data, **growth_data}
        score = 0
        for metric, weight in weights.items():
            value = combined_data.get(metric, 0)
            if isinstance(value, (int, float)):
                score += (value * weight)
        return round(score, 2)

    def extract_financial_data(self, soup):
        data = {
            "Market Cap (₹ Cr.)": 'N/A', "Current Price (₹)": 'N/A', "High (₹)": 'N/A',
            "Low (₹)": 'N/A', "Stock P/E": 0.0, "Book Value (₹)": 0.0,
            "Dividend Yield (%)": 0.0, "ROCE (%)": 0.0, "ROE (%)": 0.0, "Face Value (₹)": 0.0,
            "Debt to equity": 0.0, "Piotroski score": 0, "G Factor": 0
        }
        try:
            ratios_list = soup.select('#top-ratios > li')
            for item in ratios_list:
                name_elem = item.select_one('span.name')
                value_elem = item.select_one('span.nowrap.value') or item.select_one('span.number')
                if name_elem and value_elem:
                    name = name_elem.text.strip()
                    value = value_elem.text.strip()
                    if "Market Cap" in name: data["Market Cap (₹ Cr.)"] = self._to_float(value)
                    elif "Current Price" in name: data["Current Price (₹)"] = self._to_float(value)
                    elif "High / Low" in name:
                        parts = value.replace('₹', '').split('/')
                        if len(parts) == 2:
                            data["High (₹)"] = self._to_float(parts[0])
                            data["Low (₹)"] = self._to_float(parts[1])
                    elif "Stock P/E" in name: data["Stock P/E"] = self._to_float(value)
                    elif "Book Value" in name: data["Book Value (₹)"] = self._to_float(value)
                    elif "Dividend Yield" in name: data["Dividend Yield (%)"] = self._to_float(value)
                    elif "ROCE" in name: data["ROCE (%)"] = self._to_float(value)
                    elif "ROE" in name: data["ROE (%)"] = self._to_float(value)
                    elif "Face Value" in name: data["Face Value (₹)"] = self._to_float(value)
                    elif "Debt to equity" in name: data["Debt to equity"] = self._to_float(value)
                    elif "Piotroski score" in name: data["Piotroski score"] = int(self._to_float(value))
                    elif "G Factor" in name: data["G Factor"] = int(self._to_float(value))

        except Exception as e:
            self.update_status.emit(f"    > Could not parse financial data: {e}")
        return data

    def extract_growth_data(self, soup):
        data = {
            'Compounded Sales Growth 10 years (%)': 0.0, 'Compounded Sales Growth 5 years (%)': 0.0,
            'Compounded Sales Growth 3 years (%)': 0.0, 'Compounded Sales Growth TTM (%)': 0.0,
            'Compounded Profit Growth 10 years (%)': 0.0, 'Compounded Profit Growth 5 years (%)': 0.0,
            'Compounded Profit Growth 3 years (%)': 0.0, 'Compounded Profit Growth TTM (%)': 0.0,
            'Stock Price CAGR 10 years (%)': 0.0, 'Stock Price CAGR 5 years (%)': 0.0,
            'Stock Price CAGR 3 years (%)': 0.0, 'Stock Price CAGR 1 Year (%)': 0.0,
            'Return on Equity 10 years (%)': 0.0, 'Return on Equity 5 years (%)': 0.0,
            'Return on Equity 3 years (%)': 0.0, 'Return on Equity Last year (%)': 0.0
        }
        try:
            growth_tables = soup.select('table.ranges-table')
            for table in growth_tables:
                header = table.find('th')
                if not header: continue
                title = header.text.strip()
                rows = table.find_all('tr')[1:]
                for row in rows:
                    cols = row.find_all('td')
                    if len(cols) == 2:
                        period, value = cols[0].text.strip(), cols[1].text.strip()
                        if 'Compounded Sales Growth' in title:
                            if '10 Years' in period: data['Compounded Sales Growth 10 years (%)'] = self._to_float(value)
                            elif '5 Years' in period: data['Compounded Sales Growth 5 years (%)'] = self._to_float(value)
                            elif '3 Years' in period: data['Compounded Sales Growth 3 years (%)'] = self._to_float(value)
                            elif 'TTM' in period: data['Compounded Sales Growth TTM (%)'] = self._to_float(value)
                        elif 'Compounded Profit Growth' in title:
                            if '10 Years' in period: data['Compounded Profit Growth 10 years (%)'] = self._to_float(value)
                            elif '5 Years' in period: data['Compounded Profit Growth 5 years (%)'] = self._to_float(value)
                            elif '3 Years' in period: data['Compounded Profit Growth 3 years (%)'] = self._to_float(value)
                            elif 'TTM' in period: data['Compounded Profit Growth TTM (%)'] = self._to_float(value)
                        elif 'Stock Price CAGR' in title:
                            if '10 Years' in period: data['Stock Price CAGR 10 years (%)'] = self._to_float(value)
                            elif '5 Years' in period: data['Stock Price CAGR 5 years (%)'] = self._to_float(value)
                            elif '3 Years' in period: data['Stock Price CAGR 3 years (%)'] = self._to_float(value)
                            elif '1 Year' in period: data['Stock Price CAGR 1 Year (%)'] = self._to_float(value)
                        elif 'Return on Equity' in title:
                            if '10 Years' in period: data['Return on Equity 10 years (%)'] = self._to_float(value)
                            elif '5 Years' in period: data['Return on Equity 5 years (%)'] = self._to_float(value)
                            elif '3 Years' in period: data['Return on Equity 3 years (%)'] = self._to_float(value)
                            elif 'Last Year' in period: data['Return on Equity Last year (%)'] = self._to_float(value)
        except Exception as e:
            self.update_status.emit(f"    > Could not parse growth data: {e}")
        return data

    def extract_additional_data(self, soup):
        data = {
            "OPM (%)": 0.0,
            "FII holding (%)": 0.0,
            "DII holding (%)": 0.0,
        }
        try:
            # Extract OPM from the quarterly results table
            quarters_table = soup.select_one("div#quarters table")
            if quarters_table:
                for row in quarters_table.find_all("tr"):
                    cells = row.find_all("td")
                    if cells and "OPM %" in cells[0].text:
                        # Get the last cell value which is the latest quarter
                        data["OPM (%)"] = self._to_float(cells[-1].text)
                        break
            
            # Extract FII/DII holdings
            shareholding_table = soup.select_one("div#shareholding table")
            if shareholding_table:
                for row in shareholding_table.find_all("tr"):
                    cells = row.find_all("td")
                    if not cells: continue
                    
                    row_title = cells[0].text.strip()
                    if "FIIs" in row_title:
                        data["FII holding (%)"] = self._to_float(cells[1].text)
                    elif "DIIs" in row_title:
                        data["DII holding (%)"] = self._to_float(cells[1].text)

        except Exception as e:
            self.update_status.emit(f"    > Could not parse additional data (OPM, Holdings): {e}")
        return data

    def extract_key_insights(self, stock_ticker):
        urls_to_try = [
            f"https://www.screener.in/company/{stock_ticker}/consolidated/",
            f"https://www.screener.in/company/{stock_ticker}/"
        ]
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        
        final_data = None

        for i, url in enumerate(urls_to_try):
            if self._stop: break
            self.update_status.emit(f"    > Trying URL pattern #{i+1}...")
            response = self.fetch_with_retry(url, headers)

            if response is None:
                self.update_status.emit(f"    > Failed to fetch from {url}. Trying next...")
                continue

            soup = BeautifulSoup(response.content, 'html.parser')
            
            try:
                top_financials = self.extract_financial_data(soup)
                
                current_price = top_financials.get("Current Price (₹)", 0.0)
                if isinstance(current_price, (int, float)) and current_price > 0:
                    company_name_elem = soup.select_one('h1.h2')
                    company_name = company_name_elem.text.strip() if company_name_elem else 'N/A'
                    about_section_elem = soup.select_one('div.company-profile div.sub.show-more-box.about')
                    about_section = about_section_elem.text.strip() if about_section_elem else 'No description available.'
                    
                    growth_data = self.extract_growth_data(soup)
                    additional_data = self.extract_additional_data(soup)

                    financial_for_score = {k: v for k, v in top_financials.items() if isinstance(v, (int, float))}
                    fundamental_score = self.calculate_fundamental_score(financial_for_score, growth_data)
                    
                    final_data = {
                        "Company Name": company_name,
                        "About": about_section,
                        "Current Price (₹)": top_financials.get("Current Price (₹)"),
                        "Market Cap (₹ Cr.)": top_financials.get("Market Cap (₹ Cr.)"),
                        "High (₹)": top_financials.get("High (₹)"),
                        "Low (₹)": top_financials.get("Low (₹)"),
                        "Stock P/E": top_financials.get("Stock P/E"),
                        "Book Value (₹)": top_financials.get("Book Value (₹)"),
                        "Dividend Yield (%)": top_financials.get("Dividend Yield (%)"),
                        "ROCE (%)": top_financials.get("ROCE (%)"),
                        "ROE (%)": top_financials.get("ROE (%)"),
                        "Face Value (₹)": top_financials.get("Face Value (₹)"),
                        "Debt to equity": top_financials.get("Debt to equity"),
                        "Piotroski score": top_financials.get("Piotroski score"),
                        "G Factor": top_financials.get("G Factor"),
                        "OPM (%)": additional_data.get("OPM (%)"),
                        "Sales growth (%)": growth_data.get('Compounded Sales Growth TTM (%)'),
                        "Profit growth (%)": growth_data.get('Compounded Profit Growth TTM (%)'),
                        "EPS Growth (%)": growth_data.get('Compounded Profit Growth TTM (%)'), # Using profit growth as proxy
                        "FII holding (%)": additional_data.get("FII holding (%)"),
                        "DII holding (%)": additional_data.get("DII holding (%)"),
                        **growth_data, 
                        "Fundamental Score": fundamental_score
                    }
                    self.update_status.emit(f"    > Success with URL pattern #{i+1}.")
                    break 
                else:
                    self.update_status.emit(f"    > No valid price found with URL pattern #{i+1}. Trying next...")

            except Exception as e:
                self.update_status.emit(f"    > Error parsing data for {stock_ticker} with URL pattern #{i+1}: {e}. Trying next...")
                continue

        if final_data is None:
            self.update_status.emit(f"    > Failed to extract valid data for {stock_ticker} from all URL patterns.")
            return None
            
        return final_data


    def run(self):
        try:
            self.update_status.emit("--- Starting the fundamental scraping process ---")
            self.update_status.emit(f"Reading symbols from '{os.path.basename(self.input_path)}'...")
            if self.input_path.lower().endswith('.csv'):
                df_input = pd.read_csv(self.input_path)
            else:
                df_input = pd.read_excel(self.input_path, sheet_name=self.input_sheet)

            symbols = df_input.iloc[:, 0].tolist()
            self.update_status.emit(f"Found {len(symbols)} stock symbols to process.")

            # Define the full list of columns for the output
            columns = [
                'Stock Symbol', 'Company Name', 'Current Price (₹)', 'Market Cap (₹ Cr.)', 'About', 
                'High (₹)', 'Low (₹)', 'Stock P/E', 'Book Value (₹)', 'Dividend Yield (%)', 
                'ROCE (%)', 'ROE (%)', 'Face Value (₹)', 'Debt to equity', 'Piotroski score', 'G Factor',
                'OPM (%)', 'Sales growth (%)', 'Profit growth (%)', 'EPS Growth (%)', 
                'FII holding (%)', 'DII holding (%)',
                'Compounded Sales Growth 10 years (%)', 'Compounded Sales Growth 5 years (%)', 'Compounded Sales Growth 3 years (%)',
                'Compounded Sales Growth TTM (%)', 'Compounded Profit Growth 10 years (%)', 'Compounded Profit Growth 5 years (%)',
                'Compounded Profit Growth 3 years (%)', 'Compounded Profit Growth TTM (%)', 'Stock Price CAGR 10 years (%)',
                'Stock Price CAGR 5 years (%)', 'Stock Price CAGR 3 years (%)', 'Stock Price CAGR 1 Year (%)',
                'Return on Equity 10 years (%)', 'Return on Equity 5 years (%)', 'Return on Equity 3 years (%)',
                'Return on Equity Last year (%)', 'Fundamental Score'
            ]
            all_data = []

            for index, stock_symbol in enumerate(symbols):
                if self._stop:
                    self.update_status.emit("--- Scraping process stopped by user. ---")
                    break

                stock_symbol = str(stock_symbol).strip()
                self.update_status.emit(f"({index + 1}/{len(symbols)}) Fetching data for {stock_symbol}...")
                stock_data = self.extract_key_insights(stock_symbol)
                if stock_data:
                    # Create a list of values in the same order as the columns
                    row_data = [stock_symbol] + [stock_data.get(col, 'N/A') for col in columns[1:]]
                    all_data.append(row_data)
                    self.update_status.emit(f"    > Successfully extracted data for {stock_symbol}.")
                else:
                    self.update_status.emit(f"    > Failed to extract data for {stock_symbol}.")
                time.sleep(2)

            if all_data:
                self.update_status.emit("Creating final DataFrame...")
                df_output = pd.DataFrame(all_data, columns=columns)
                self.update_status.emit(f"Saving data to {self.output_path}...")
                df_output.to_excel(self.output_path, sheet_name=self.output_sheet, index=False)
                self.update_status.emit("--- Data fetching completed successfully! ---")
                self.data_ready.emit(df_output)
            else:
                self.update_status.emit("--- No data was collected. ---")

        except FileNotFoundError:
            self.update_status.emit(f"ERROR: Input file not found at {self.input_path}")
        except Exception as e:
            self.update_status.emit(f"An unexpected error occurred: {e}")
        finally:
            self.finished.emit()

# +++ MODIFIED AITS WORKER CLASS +++
class AITSWorker(QThread):
    log_update = pyqtSignal(str)
    dashboard_update = pyqtSignal(list)
    finished = pyqtSignal()

    def __init__(self, kite, enctoken, user_id, params):
        super().__init__()
        self.kite = kite
        self.enctoken = enctoken
        self.user_id = user_id
        self.params = params
        self.managed_trades = []
        self._is_running = True
        self._graceful_stop = False
        self.kill_switch_activated = False
        self.instrument_map = {}
        self.latest_ticks = {}
        self.kws = None
        self.state_file = "intraday_trades.json"
        
        # Extract parameters with defaults
        self.start_time = params.get('start_time', QTime(9, 15))
        self.end_time = params.get('end_time', QTime(15, 15))
        self.polling_interval = params.get('polling_interval', 1) 
        self.square_off_buffer = params.get('square_off_buffer', 5)
        self.paper_trading = params.get('paper_trading', False)


    def log(self, message):
        self.log_update.emit(message)

    def save_state(self):
        """Saves the current state of managed_trades to the JSON file with a date."""
        try:
            state = {
                "date": datetime.now().strftime('%Y-%m-%d'),
                "trades": self.managed_trades
            }
            with open(self.state_file, 'w') as f:
                json.dump(state, f, indent=4)
        except Exception as e:
            self.log(f"ERROR: Could not save state to {self.state_file}: {e}")

    def load_state(self):
        """Loads the state from the JSON file if it's for the current day."""
        if not os.path.exists(self.state_file):
            self.log("INFO: No state file found. Starting fresh.")
            return

        try:
            with open(self.state_file, 'r') as f:
                state = json.load(f)
            
            today_str = datetime.now().strftime('%Y-%m-%d')
            if state.get("date") != today_str:
                self.log(f"INFO: State file is from a previous day ({state.get('date')}). Starting fresh.")
                return

            loaded_trades = state.get("trades", [])
            resumed_count = 0
            for trade in loaded_trades:
                if trade.get('status') == 'ACTIVE':
                    self.managed_trades.append(trade)
                    resumed_count += 1
            
            if resumed_count > 0:
                self.log(f"SUCCESS: Resumed tracking for {resumed_count} active trades from {self.state_file}.")
                self.dashboard_update.emit(self.managed_trades)
        except Exception as e:
            self.log(f"ERROR: Could not load state from {self.state_file}: {e}")

    def load_trades_from_excel(self):
        try:
            self.log(f"Loading new trades from: {self.params['trade_file_path']}")
            df = pd.read_excel(self.params['trade_file_path'])
            required_cols = ['SYMBOL', 'ACTION', 'ENTRY_PRICE', 'TARGET_PRICE', 'STOPLOSS_PRICE', 'QUANTITY_TYPE', 'QUANTITY_VALUE']
            if not all(col in df.columns for col in required_cols):
                self.log(f"ERROR: Trade file is missing one of the required columns: {required_cols}")
                return False

            all_instruments = self.kite.instruments("NSE")
            self.instrument_map = {inst['tradingsymbol']: inst['instrument_token'] for inst in all_instruments}
            
            new_trades_count = 0
            for _, row in df.iterrows():
                symbol = row['SYMBOL']
                if any(t['symbol'] == symbol and t['status'] in ['PENDING', 'ACTIVE'] for t in self.managed_trades):
                    continue

                if symbol not in self.instrument_map:
                    self.log(f"WARNING: Symbol '{symbol}' not found in instrument list. Skipping.")
                    continue

                trade = {
                    "symbol": symbol,
                    "action": row['ACTION'].upper(),
                    "entry_price": float(row['ENTRY_PRICE']),
                    "target_price": float(row['TARGET_PRICE']),
                    "stoploss_price": float(row['STOPLOSS_PRICE']),
                    "quantity_type": row['QUANTITY_TYPE'].upper(),
                    "quantity_value": float(row['QUANTITY_VALUE']),
                    "quantity": 0,
                    "instrument_token": self.instrument_map[symbol],
                    "status": "PENDING", 
                    "type": "BOT", 
                    "pnl": 0.0,
                    "avg_price": 0.0,
                    "ltp": 0.0
                }
                self.managed_trades.append(trade)
                new_trades_count += 1
            
            self.log(f"Successfully loaded {new_trades_count} new trades from Excel.")
            self.dashboard_update.emit(self.managed_trades)
            self.save_state()
            return True
        except Exception as e:
            self.log(f"ERROR: Failed to load trade file: {e}")
            return False

    # --- WebSocket Handlers ---
    def on_ticks(self, ws, ticks):
        for tick in ticks:
            self.latest_ticks[tick['instrument_token']] = tick['last_price']

    def on_connect(self, ws, response):
        self.log("WebSocket: Connected")
        tokens = [trade['instrument_token'] for trade in self.managed_trades]
        if tokens:
            self.log(f"Subscribing to {len(tokens)} instrument tokens...")
            ws.subscribe(tokens)
            ws.set_mode(ws.MODE_QUOTE, tokens)

    def on_close(self, ws, code, reason):
        self.log(f"WebSocket: Closed. Code: {code}, Reason: {reason}")

    def on_error(self, ws, code, reason):
        self.log(f"WebSocket: Error. Code: {code}, Reason: {reason}")

    def run(self):
        self.load_state()
        if not self.load_trades_from_excel():
            self.finished.emit()
            return
        
        if self.paper_trading:
            self.log("--- PAPER TRADING MODE ACTIVATED ---")
        else:
            self.log("--- LIVE TRADING MODE ACTIVATED ---")

        self.log("AITS Worker starting...")
        
        self.kws = KiteTicker(api_key="kitefront", access_token=f"{self.enctoken}&user_id={self.user_id}")
        self.kws.on_ticks = self.on_ticks
        self.kws.on_connect = self.on_connect
        self.kws.on_close = self.on_close
        self.kws.on_error = self.on_error
        self.kws.connect(threaded=True)
        
        while not self.kws.is_connected():
            if not self._is_running:
                self.log("Stop requested before WebSocket connection.")
                self.finished.emit()
                return
            time.sleep(1)

        square_off_time = self.end_time.addSecs(-self.square_off_buffer * 60)
        self.log(f"Trading session: {self.start_time.toString('HH:mm:ss')} to {self.end_time.toString('HH:mm:ss')}.")
        self.log(f"Automatic square-off will begin at: {square_off_time.toString('HH:mm:ss')}.")

        while self._is_running:
            try:
                current_time = QTime.currentTime()

                if current_time < self.start_time or current_time >= self.end_time:
                    self.log("INFO: Outside trading hours. Shutting down.")
                    self.square_off_all()
                    break
                
                if current_time >= square_off_time:
                    self.log("INFO: Reached square-off time. Closing all positions.")
                    self.square_off_all()
                    break

                if self.kill_switch_activated:
                    time.sleep(self.polling_interval)
                    continue

                if not self.paper_trading:
                    self.check_pnl_and_kill_switch()

                active_trades_count = sum(1 for t in self.managed_trades if t['status'] == 'ACTIVE')
                
                for trade in self.managed_trades:
                    ltp = self.latest_ticks.get(trade['instrument_token'])
                    
                    if ltp is None:
                        continue
                    
                    trade['ltp'] = ltp

                    if trade['status'] == 'ACTIVE':
                        if trade['action'] == 'BUY':
                            trade['pnl'] = (ltp - trade['avg_price']) * trade['quantity']
                        else: # SELL
                            trade['pnl'] = (trade['avg_price'] - ltp) * trade['quantity']

                    elif trade['status'] == 'PENDING' and not self._graceful_stop:
                        if active_trades_count >= self.params['max_trades']:
                            continue
                        
                        if (trade['action'] == 'BUY' and ltp >= trade['entry_price']) or \
                           (trade['action'] == 'SELL' and ltp <= trade['entry_price']):
                            self.enter_position_bracket_order(trade)
                            active_trades_count += 1
                
                if self.params['adopt_manual'] and not self.paper_trading:
                    self.reconcile_manual_trades()
                elif not self.paper_trading:
                    self.reconcile_positions()
                    
                self.dashboard_update.emit(self.managed_trades)
                
            except Exception as e:
                self.log(f"ERROR in main logic loop: {e}")

            time.sleep(self.polling_interval)

        self.stop(hard_stop=False)
        self.log("AITS Worker has stopped.")
        self.finished.emit()

    def place_simulated_order(self, trade):
        self.log(f"PAPER TRADE: Simulating {trade['action']} order for {trade['quantity']} shares of {trade['symbol']}.")
        trade['status'] = 'ACTIVE'
        trade['avg_price'] = trade['ltp'] # Simulate fill at current market price
        self.save_state()

    def enter_position_bracket_order(self, trade):
        self.log(f"TRIGGER: Entry condition met for {trade['action']} {trade['symbol']} @ {trade['entry_price']}")
        trade['status'] = 'TRIGGERED'
        
        if trade['quantity_type'] == 'FIXED':
            trade['quantity'] = int(trade['quantity_value'])
        elif trade['quantity_type'] == 'CAPITAL':
            trade['quantity'] = int(trade['quantity_value'] / trade['ltp'])
        
        if trade['quantity'] <= 0:
            self.log(f"WARNING: Calculated quantity is 0 or less for {trade['symbol']}. Skipping entry.")
            trade['status'] = 'ERROR'
            self.save_state()
            return
        
        if self.paper_trading:
            self.place_simulated_order(trade)
            return

        try:
            squareoff_diff = abs(trade['target_price'] - trade['entry_price'])
            stoploss_diff = abs(trade['entry_price'] - trade['stoploss_price'])

            order_id = self.kite.place_order(
                variety=self.kite.VARIETY_BO,
                exchange=self.kite.EXCHANGE_NSE,
                tradingsymbol=trade['symbol'],
                transaction_type=self.kite.TRANSACTION_TYPE_BUY if trade['action'] == 'BUY' else self.kite.TRANSACTION_TYPE_SELL,
                quantity=trade['quantity'],
                product=self.kite.PRODUCT_MIS,
                order_type=self.kite.ORDER_TYPE_MARKET,
                squareoff=round(squareoff_diff),
                stoploss=round(stoploss_diff)
            )
            self.log(f"SUCCESS: Placed BRACKET order for {trade['symbol']}. Order ID: {order_id}")
            trade['status'] = 'ACTIVE'
            
            time.sleep(1)
            positions = self.kite.positions().get('net', [])
            for pos in positions:
                if pos['tradingsymbol'] == trade['symbol']:
                    trade['avg_price'] = pos['average_price']
                    self.log(f"INFO: Updated avg_price for {trade['symbol']} to {trade['avg_price']}")
                    break
            
            self.save_state()

        except Exception as e:
            self.log(f"ERROR: Failed to place bracket order for {trade['symbol']}: {e}")
            trade['status'] = 'ERROR'
            self.save_state()

    def reconcile_positions(self):
        if self.paper_trading:
            return
        try:
            positions = self.kite.positions().get('net', [])
            active_symbols_in_broker = {pos['tradingsymbol'] for pos in positions if pos.get('product') == self.kite.PRODUCT_MIS and pos.get('quantity', 0) != 0}

            for trade in self.managed_trades:
                if trade['status'] == 'ACTIVE' and trade['symbol'] not in active_symbols_in_broker:
                    self.log(f"RECONCILE: Position for {trade['symbol']} is closed. Updating status.")
                    trade['status'] = 'CLOSED'
                    if trade['avg_price'] > 0:
                        if trade['action'] == 'BUY':
                            trade['pnl'] = (trade['ltp'] - trade['avg_price']) * trade['quantity']
                        else:
                            trade['pnl'] = (trade['avg_price'] - trade['ltp']) * trade['quantity']
                    self.save_state()
        except Exception as e:
            self.log(f"ERROR: Could not reconcile positions: {e}")
            
    def reconcile_manual_trades(self):
        """Adopts manual trades and reconciles existing ones."""
        if self.paper_trading:
            return
        try:
            positions = self.kite.positions().get('net', [])
            broker_positions = {pos['tradingsymbol']: pos for pos in positions if pos.get('product') == self.kite.PRODUCT_MIS and pos.get('quantity', 0) != 0}
            
            # 1. Adopt new manual trades
            for symbol, pos in broker_positions.items():
                if not any(t['symbol'] == symbol and t['status'] == 'ACTIVE' for t in self.managed_trades):
                    self.log(f"ADOPT: Detected new manual position for {symbol}. Adopting now.")
                    
                    action = 'BUY' if pos['quantity'] > 0 else 'SELL'
                    avg_price = pos['average_price']
                    
                    # Calculate default SL and Target
                    sl_pct = self.params['manual_sl_perc'] / 100
                    tgt_pct = self.params['manual_target_perc'] / 100
                    
                    if action == 'BUY':
                        stoploss_price = avg_price * (1 - sl_pct)
                        target_price = avg_price * (1 + tgt_pct)
                    else: # SELL
                        stoploss_price = avg_price * (1 + sl_pct)
                        target_price = avg_price * (1 - tgt_pct)

                    trade = {
                        "symbol": symbol,
                        "action": action,
                        "entry_price": avg_price,
                        "target_price": round(target_price),
                        "stoploss_price": round(stoploss_price),
                        "quantity": abs(pos['quantity']),
                        "instrument_token": self.instrument_map.get(symbol),
                        "status": "ACTIVE", 
                        "type": "MANUAL", 
                        "pnl": pos['pnl'],
                        "avg_price": avg_price,
                        "ltp": pos['last_price']
                    }
                    self.managed_trades.append(trade)
                    self.log(f"INFO: Adopting {symbol} with Target: {trade['target_price']}, SL: {trade['stoploss_price']}")

            # 2. Reconcile existing trades
            self.reconcile_positions()

        except Exception as e:
            self.log(f"ERROR: Could not reconcile manual trades: {e}")


    def check_pnl_and_kill_switch(self):
        try:
            positions = self.kite.positions().get('net', [])
            open_pnl = sum(pos.get('pnl', 0.0) for pos in positions if pos.get('product') == self.kite.PRODUCT_MIS)
            closed_pnl = sum(trade.get('pnl', 0.0) for trade in self.managed_trades if trade['status'] == 'CLOSED')
            total_pnl = open_pnl + closed_pnl

            if total_pnl <= self.params['loss_limit']:
                self.log(f"!!! KILL SWITCH ACTIVATED !!! Total P&L ({total_pnl:.2f}) has breached the daily limit ({self.params['loss_limit']}).")
                self.kill_switch_activated = True
                self.square_off_all()
        except Exception as e:
            self.log(f"ERROR: Could not check P&L: {e}")

    def cancel_all_pending_orders(self):
        self.log("INFO: Cancelling all pending entry orders.")
        for trade in self.managed_trades:
            if trade['status'] == 'PENDING':
                trade['status'] = 'CANCELLED'
        self.save_state()

    def square_off_all(self):
        self.log("--- INITIATING SQUARE OFF ALL ---")
        self.cancel_all_pending_orders()
        
        if self.paper_trading:
            for trade in self.managed_trades:
                if trade['status'] == 'ACTIVE':
                    trade['status'] = 'CLOSED'
                    self.log(f"PAPER TRADE: Squaring off {trade['symbol']}.")
            self.save_state()
            return
        
        try:
            positions = self.kite.positions().get('net', [])
            for pos in positions:
                if pos.get('product') == self.kite.PRODUCT_MIS and pos.get('quantity', 0) != 0:
                    symbol = pos['tradingsymbol']
                    qty = abs(pos['quantity'])
                    action = self.kite.TRANSACTION_TYPE_SELL if pos['quantity'] > 0 else self.kite.TRANSACTION_TYPE_BUY
                    self.log(f"Squaring off {qty} of {symbol}...")
                    self.kite.place_order(
                        variety=self.kite.VARIETY_REGULAR, exchange=self.kite.EXCHANGE_NSE,
                        tradingsymbol=symbol, transaction_type=action, quantity=qty,
                        product=self.kite.PRODUCT_MIS, order_type=self.kite.ORDER_TYPE_MARKET
                    )
                    for trade in self.managed_trades:
                        if trade['symbol'] == symbol and trade['status'] == 'ACTIVE':
                            trade['status'] = 'CLOSED'
                            self.log(f"Marking trade for {symbol} as CLOSED due to square off.")
            self.save_state()

        except Exception as e:
            self.log(f"ERROR during square off: {e}")
    
    def graceful_stop(self):
        self.log("Graceful stop initiated. No new trades will be placed.")
        self._graceful_stop = True
        self.cancel_all_pending_orders()

    def stop(self, hard_stop=True):
        self.log("Stop signal received. Shutting down worker...")
        if hard_stop:
            self.square_off_all()
        self._is_running = False
        if self.kws and self.kws.is_connected():
            self.log("Closing WebSocket connection...")
            self.kws.close(1000, "Manual stop")

class StockApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Stock Trading App")
        self.setGeometry(100, 100, 1200, 800)

        self.main_widget = QWidget()
        self.setCentralWidget(self.main_widget)
        self.layout = QVBoxLayout(self.main_widget)

        self.token_layout = QHBoxLayout()
        self.token_label = QLabel("Authorization Token:")
        self.token_input = QLineEdit()
        self.token_button = QPushButton("Update Token")
        self.token_button.clicked.connect(self.update_token)
        self.token_layout.addWidget(self.token_label)
        self.token_layout.addWidget(self.token_input)
        self.token_layout.addWidget(self.token_button)
        self.layout.addLayout(self.token_layout)

        self.tabs = QTabWidget()
        self.layout.addWidget(self.tabs)

        self.kite = None
        self.aits_worker = None

        self.status_area = QTextEdit()
        self.status_area.setReadOnly(True)
        self.status_area.setMaximumHeight(150)
        self.layout.addWidget(self.status_area)
        self.update_status("Please enter and update your token to begin.")

        self.load_config()

        self.setup_profile_tab()
        self.setup_orders_tab()
        self.setup_positions_tab()
        self.setup_holdings_tab()
        self.setup_instruments_tab()
        self.setup_gtt_tab()
        self.setup_margins_tab()
        self.setup_historical_tab()
        self.setup_stoploss_tab()
        self.setup_target_tab()
        self.setup_fundamental_tab()
        self.setup_aits_tab() 
        self.setup_tradeplan_tab() # New TradePlan Tab


    def create_description_label(self, text):
        desc_label = QLabel(text)
        desc_label.setWordWrap(True)
        desc_label.setStyleSheet("background-color: #f0f8ff; border: 1px solid #cce5ff; padding: 8px; border-radius: 4px;")
        desc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return desc_label

    def load_config(self):
        config_file = os.path.join(os.getcwd(), "config.json")
        if os.path.exists(config_file):
            try:
                with open(config_file, 'r') as f:
                    config = json.load(f)

                token = config.get("enctoken", "")
                last_date = config.get("date", "")
                current_date = datetime.now().strftime('%Y-%m-%d')
                if token and last_date == current_date:
                    self.token_input.setText(token)
                    self.update_status("Loaded last used token from config.json.")
                    self.update_token()
                else:
                    self.update_status("No valid token for today found in config.json.")

                if hasattr(self, 'fundamental_input_path_input'):
                    self.fundamental_input_path_input.setText(config.get("fundamental_input_path", ""))
                    self.update_status("Loaded paths for Fundamental Scraper.")

            except Exception as e:
                self.update_status(f"Error loading config.json: {str(e)}")

    def save_config(self):
        config_file = os.path.join(os.getcwd(), "config.json")
        try:
            if os.path.exists(config_file):
                with open(config_file, 'r') as f:
                    config = json.load(f)
            else:
                config = {}
        except Exception:
            config = {}

        config["enctoken"] = self.token_input.text().strip()
        config["date"] = datetime.now().strftime('%Y-%m-%d')
        if hasattr(self, 'fundamental_input_path_input'):
            config["fundamental_input_path"] = self.fundamental_input_path_input.text()

        try:
            with open(config_file, 'w') as f:
                json.dump(config, f, indent=4)
            self.update_status("Configuration saved to config.json.")
        except Exception as e:
            self.update_status(f"Error saving config.json: {str(e)}")

    def update_token(self):
        token = self.token_input.text().strip()
        if token:
            try:
                self.kite = KiteApp(token)
                profile = self.kite.profile()
                self.update_status(f"Token updated successfully! User: {profile.get('user_name', 'Unknown')}")
                self.save_config()
            except Exception as e:
                self.update_status(f"Error updating token: {str(e)}")
                QMessageBox.critical(self, "Error", f"Invalid token: {str(e)}")
        else:
            self.update_status("Please enter a valid token.")
            QMessageBox.warning(self, "Warning", "Token field is empty!")

    def closeEvent(self, event):
        self.save_config()
        if self.aits_worker and self.aits_worker.isRunning():
            self.stop_aits_worker()
        event.accept()

    def check_kite_initialized(self):
        if self.kite is None:
            self.update_status("Error: Please update the token first!")
            QMessageBox.warning(self, "Warning", "Token not set. Please update the token.")
            return False
        return True

    def update_status(self, message):
        self.status_area.append(f"{datetime.now().strftime('%H:%M:%S')} - {message}")


    def save_to_excel(self, sheet_name, data_frame):
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            # Use get_main_dir() to stay out of the Temp folder
            excel_path = os.path.join(os.getcwd(), f"{sheet_name}_{timestamp}.xlsx")
            with pd.ExcelWriter(excel_path, engine='openpyxl') as writer:
                data_frame.to_excel(writer, sheet_name=sheet_name, index=False)
            self.update_status(f"Data saved to {excel_path}")
        except Exception as e:
            self.update_status(f"Error saving to Excel: {str(e)}")
            QMessageBox.critical(self, "Error", f"Failed to save data: {str(e)}")

    def setup_profile_tab(self):
        self.profile_tab = QWidget()
        self.profile_layout = QVBoxLayout(self.profile_tab)

        description = self.create_description_label("View your account profile information, including user details, enabled exchanges, and available products.")
        self.profile_layout.addWidget(description)

        self.profile_grid = QGridLayout()
        self.profile_labels = {}
        fields = ["email", "user_name", "broker", "exchanges", "products", "order_types"]
        for i, field in enumerate(fields):
            label = QLabel(f"{field.capitalize().replace('_', ' ')}:")
            value = QLabel("")
            self.profile_labels[field] = value
            self.profile_grid.addWidget(label, i, 0)
            self.profile_grid.addWidget(value, i, 1)
        self.profile_layout.addLayout(self.profile_grid)
        self.profile_refresh_btn = QPushButton("Refresh")
        self.profile_refresh_btn.clicked.connect(self.refresh_profile)
        self.profile_save_btn = QPushButton("Save to Excel")
        self.profile_save_btn.clicked.connect(lambda: self.save_profile_to_excel())
        self.profile_btn_layout = QHBoxLayout()
        self.profile_btn_layout.addWidget(self.profile_refresh_btn)
        self.profile_btn_layout.addWidget(self.profile_save_btn)
        self.profile_layout.addLayout(self.profile_btn_layout)
        self.tabs.addTab(self.profile_tab, "Profile")

    def refresh_profile(self):
        if not self.check_kite_initialized():
            return
        try:
            data = self.kite.profile()
            filtered_data = {key: data.get(key, "") for key in self.profile_labels}
            for key, value in filtered_data.items():
                self.profile_labels[key].setText(str(value))
            self.update_status("Profile data refreshed.")
        except Exception as e:
            self.update_status(f"Error fetching profile: {str(e)}")

    def save_profile_to_excel(self):
        if not self.check_kite_initialized():
            return
        data = self.kite.profile()
        filtered_data = {key: data.get(key, "") for key in self.profile_labels}
        df = pd.DataFrame([filtered_data])
        self.save_to_excel("Profile", df)

    def setup_orders_tab(self):
        self.orders_tab = QWidget()
        self.orders_layout = QVBoxLayout(self.orders_tab)

        description = self.create_description_label("See a list of all your recent orders, including their status (e.g., completed, pending, cancelled).")
        self.orders_layout.addWidget(description)

        self.orders_table = QTableWidget()
        self.orders_table.setColumnCount(10)
        self.orders_table.setHorizontalHeaderLabels(["Timestamp", "Type", "Symbol", "Status", "Exchange",
                                                     "Token", "Order Type", "Product", "Quantity", "Price"])
        self.orders_table.horizontalHeader().setStretchLastSection(True)
        self.orders_layout.addWidget(self.orders_table)
        self.orders_refresh_btn = QPushButton("Refresh")
        self.orders_refresh_btn.clicked.connect(self.refresh_orders)
        self.orders_save_btn = QPushButton("Save to Excel")
        self.orders_save_btn.clicked.connect(lambda: self.save_orders_to_excel())
        self.orders_btn_layout = QHBoxLayout()
        self.orders_btn_layout.addWidget(self.orders_refresh_btn)
        self.orders_btn_layout.addWidget(self.orders_save_btn)
        self.orders_layout.addLayout(self.orders_btn_layout)
        self.tabs.addTab(self.orders_tab, "Orders")

    def refresh_orders(self):
        if not self.check_kite_initialized():
            return
        try:
            orders = self.kite.orders()
            self.orders_table.setRowCount(len(orders))
            for i, order in enumerate(orders):
                fields = ["order_timestamp", "transaction_type", "tradingsymbol", "status", "exchange",
                          "instrument_token", "order_type", "product", "quantity", "price"]
                for j, field in enumerate(fields):
                    self.orders_table.setItem(i, j, QTableWidgetItem(str(order.get(field, ""))))
            self.update_status("Orders data refreshed.")
        except Exception as e:
            self.update_status(f"Error fetching orders: {str(e)}")

    def save_orders_to_excel(self):
        if not self.check_kite_initialized():
            return
        orders = self.kite.orders()
        filtered_orders = [{field: order.get(field, "") for field in ["order_timestamp", "transaction_type",
                                                                     "tradingsymbol", "status", "exchange", "instrument_token", "order_type",
                                                                     "product", "quantity", "price"]} for order in orders]
        df = pd.DataFrame(filtered_orders)
        self.save_to_excel("Orders", df)

    def setup_positions_tab(self):
        self.positions_tab = QWidget()
        self.positions_layout = QVBoxLayout(self.positions_tab)

        description = self.create_description_label("View your current open positions for the day, including quantity, average price, and real-time profit/loss (PnL).")
        self.positions_layout.addWidget(description)

        self.positions_table = QTableWidget()
        self.positions_table.setColumnCount(7)
        self.positions_table.setHorizontalHeaderLabels(["Symbol", "Exchange", "Product", "Quantity",
                                                        "Avg Price", "Last Price", "PnL"])
        self.positions_table.horizontalHeader().setStretchLastSection(True)
        self.positions_layout.addWidget(self.positions_table)
        self.positions_refresh_btn = QPushButton("Refresh")
        self.positions_refresh_btn.clicked.connect(self.refresh_positions)
        self.positions_save_btn = QPushButton("Save to Excel")
        self.positions_save_btn.clicked.connect(lambda: self.save_positions_to_excel())
        self.positions_btn_layout = QHBoxLayout()
        self.positions_btn_layout.addWidget(self.positions_refresh_btn)
        self.positions_btn_layout.addWidget(self.positions_save_btn)
        self.positions_layout.addLayout(self.positions_btn_layout)
        self.tabs.addTab(self.positions_tab, "Positions")

    def refresh_positions(self):
        if not self.check_kite_initialized():
            return
        try:
            positions = self.kite.positions().get('net', [])
            if not isinstance(positions, list):
                self.update_status(f"Invalid positions data: Expected list, got {type(positions)} - {positions}")
                positions = []
            self.positions_table.setRowCount(len(positions))
            for i, pos in enumerate(positions):
                fields = ["tradingsymbol", "exchange", "product", "quantity", "average_price", "last_price", "pnl"]
                for j, field in enumerate(fields):
                    self.positions_table.setItem(i, j, QTableWidgetItem(str(pos.get(field, ""))))
            self.update_status("Positions data refreshed.")
        except Exception as e:
            self.update_status(f"Error fetching positions: {str(e)}")

    def save_positions_to_excel(self):
        if not self.check_kite_initialized():
            return
        positions = self.kite.positions().get('net', [])
        if not isinstance(positions, list):
            self.update_status(f"Invalid positions data: Expected list, got {type(positions)} - {positions}")
            positions = []
        filtered_positions = [{field: pos.get(field, "") for field in ["tradingsymbol", "exchange", "product",
                                                                        "quantity", "average_price", "last_price", "pnl"]} for pos in positions]
        df = pd.DataFrame(filtered_positions)
        self.save_to_excel("Positions", df)

    def setup_holdings_tab(self):
        self.holdings_tab = QWidget()
        self.holdings_layout = QVBoxLayout(self.holdings_tab)

        description = self.create_description_label("View your long-term investments (holdings). This shows all the stocks you own in your demat account.")
        self.holdings_layout.addWidget(description)

        self.holdings_table = QTableWidget()
        self.holdings_table.setColumnCount(7)
        self.holdings_table.setHorizontalHeaderLabels(["Symbol", "Exchange", "Token", "Quantity",
                                                       "Avg Price", "Last Price", "PnL"])
        self.holdings_table.horizontalHeader().setStretchLastSection(True)
        self.holdings_layout.addWidget(self.holdings_table)
        self.holdings_refresh_btn = QPushButton("Refresh")
        self.holdings_refresh_btn.clicked.connect(self.refresh_holdings)
        self.holdings_save_btn = QPushButton("Save to Excel")
        self.holdings_save_btn.clicked.connect(lambda: self.save_holdings_to_excel())
        self.holdings_btn_layout = QHBoxLayout()
        self.holdings_btn_layout.addWidget(self.holdings_refresh_btn)
        self.holdings_btn_layout.addWidget(self.holdings_save_btn)
        self.holdings_layout.addLayout(self.holdings_btn_layout)
        self.tabs.addTab(self.holdings_tab, "Holdings")

    def refresh_holdings(self):
        if not self.check_kite_initialized():
            return
        try:
            holdings = self.kite.holdings()
            if not isinstance(holdings, list):
                self.update_status(f"Invalid holdings data: Expected list, got {type(holdings)} - {holdings}")
                holdings = []
            self.holdings_table.setRowCount(len(holdings))
            for i, hol in enumerate(holdings):
                fields = ["tradingsymbol", "exchange", "instrument_token", "quantity", "average_price", "last_price", "pnl"]
                for j, field in enumerate(fields):
                    self.holdings_table.setItem(i, j, QTableWidgetItem(str(hol.get(field, ""))))
            self.update_status("Holdings data refreshed.")
        except Exception as e:
            self.update_status(f"Error fetching holdings: {str(e)}")

    def save_holdings_to_excel(self):
        if not self.check_kite_initialized():
            return
        holdings = self.kite.holdings()
        if not isinstance(holdings, list):
            self.update_status(f"Invalid holdings data: Expected list, got {type(holdings)} - {holdings}")
            holdings = []
        filtered_holdings = [{field: hol.get(field, "") for field in ["tradingsymbol", "exchange", "instrument_token",
                                                                       "quantity", "average_price", "last_price", "pnl"]} for hol in holdings]
        df = pd.DataFrame(filtered_holdings)
        self.save_to_excel("Holdings", df)

    def setup_instruments_tab(self):
        self.instruments_tab = QWidget()
        self.instruments_layout = QVBoxLayout(self.instruments_tab)

        description = self.create_description_label("Fetch and view a list of all tradable instruments (stocks, derivatives) for a selected exchange.")
        self.instruments_layout.addWidget(description)

        self.instruments_exchange_layout = QHBoxLayout()
        self.instruments_exchange_label = QLabel("Exchange:")
        self.instruments_exchange = QComboBox()
        self.instruments_exchange.addItems(["BSE", "NSE", "NFO", "CDS", "BFO", "MCX"])
        self.instruments_exchange_layout.addWidget(self.instruments_exchange_label)
        self.instruments_exchange_layout.addWidget(self.instruments_exchange)
        self.instruments_layout.addLayout(self.instruments_exchange_layout)
        self.instruments_table = QTableWidget()
        self.instruments_table.setColumnCount(4)
        self.instruments_table.setHorizontalHeaderLabels(["Symbol", "Token", "Name", "Segment"])
        self.instruments_table.horizontalHeader().setStretchLastSection(True)
        self.instruments_layout.addWidget(self.instruments_table)
        self.instruments_refresh_btn = QPushButton("Refresh")
        self.instruments_refresh_btn.clicked.connect(self.refresh_instruments)
        self.instruments_save_btn = QPushButton("Save to Excel")
        self.instruments_save_btn.clicked.connect(lambda: self.save_instruments_to_excel())
        self.instruments_btn_layout = QHBoxLayout()
        self.instruments_btn_layout.addWidget(self.instruments_refresh_btn)
        self.instruments_btn_layout.addWidget(self.instruments_save_btn)
        self.instruments_layout.addLayout(self.instruments_btn_layout)
        self.tabs.addTab(self.instruments_tab, "Instruments")

    def refresh_instruments(self):
        if not self.check_kite_initialized():
            return
        try:
            exchange = self.instruments_exchange.currentText()
            instruments = self.kite.instruments(exchange)
            filtered_instruments = [inst for inst in instruments if inst['lot_size'] < 2 and
                                      all(x not in inst['tradingsymbol'] for x in ["-X1", "-P1", "-RE", "-W1", "-IV", "-BZ", "-GS", "-GB", "-RR", "-E1"]) and
                                      inst['name'] and all(x not in inst['name'] for x in ["HDFCAMC", "GOI LOAN", "GOLDBOND", "AXISAMC",
                                      "BIRLASLAMC", "DSPAMC", "EDELAMC", "ICICIPRAMC", "KOTAKMAMC", "LICNAMC", "MIRAEAMC",
                                      "MOTILALAMC", "SBIAMC", "TATAAML", "UTIAMC", "ZERODHAAMC"])]
            self.instruments_table.setRowCount(len(filtered_instruments))
            for i, inst in enumerate(filtered_instruments):
                fields = ["tradingsymbol", "instrument_token", "name", "segment"]
                for j, field in enumerate(fields):
                    self.instruments_table.setItem(i, j, QTableWidgetItem(str(inst.get(field, ""))))
            self.update_status(f"Instruments data refreshed for {exchange}.")
        except Exception as e:
            self.update_status(f"Error fetching instruments: {str(e)}")

    def save_instruments_to_excel(self):
        if not self.check_kite_initialized():
            return
        exchange = self.instruments_exchange.currentText()
        instruments = self.kite.instruments(exchange)
        filtered_instruments = [{field: inst.get(field, "") for field in ["tradingsymbol", "instrument_token", "name", "segment"]}
                                for inst in instruments if inst['lot_size'] < 2 and
                                all(x not in inst['tradingsymbol'] for x in ["-X1", "-P1", "-RE", "-W1", "-IV", "-BZ", "-GS", "-GB", "-RR", "-E1"]) and
                                inst['name'] and all(x not in inst['name'] for x in ["HDFCAMC", "GOI LOAN", "GOLDBOND", "AXISAMC",
                                "BIRLASLAMC", "DSPAMC", "EDELAMC", "ICICIPRAMC", "KOTAKMAMC", "LICNAMC", "MIRAEAMC",
                                "MOTILALAMC", "SBIAMC", "TATAAML", "UTIAMC", "ZERODHAAMC"])]
        df = pd.DataFrame(filtered_instruments)
        df.columns = ["SYMBOL", "TOKEN", "name", "segment"]
        self.save_to_excel("Instruments", df)

    def setup_gtt_tab(self):
        self.gtt_tab = QWidget()
        self.gtt_layout = QVBoxLayout(self.gtt_tab)

        description = self.create_description_label("Manage your Good-Till-Triggered (GTT) orders. These are orders that remain active until a specific trigger price is met.")
        self.gtt_layout.addWidget(description)

        self.gtt_table = QTableWidget()
        self.gtt_table.setColumnCount(7)
        self.gtt_table.setHorizontalHeaderLabels(["ID", "Status", "Trigger Type", "Symbol", "Exchange", "Price", "Quantity"])
        self.gtt_table.horizontalHeader().setStretchLastSection(True)
        self.gtt_layout.addWidget(self.gtt_table)
        self.gtt_refresh_btn = QPushButton("Refresh")
        self.gtt_refresh_btn.clicked.connect(self.refresh_gtt)
        self.gtt_save_btn = QPushButton("Save to Excel")
        self.gtt_save_btn.clicked.connect(lambda: self.save_gtt_to_excel())
        self.gtt_btn_layout = QHBoxLayout()
        self.gtt_btn_layout.addWidget(self.gtt_refresh_btn)
        self.gtt_btn_layout.addWidget(self.gtt_save_btn)
        self.gtt_layout.addLayout(self.gtt_btn_layout)
        self.tabs.addTab(self.gtt_tab, "GTT")

    def refresh_gtt(self):
        if not self.check_kite_initialized():
            return
        try:
            gtt = self.kite.get_gtts()
            if not isinstance(gtt, list):
                self.update_status(f"Invalid GTT data: Expected list, got {type(gtt)} - {gtt}")
                gtt = []
            self.gtt_table.setRowCount(len(gtt))
            for i, item in enumerate(gtt):
                fields = ["id", "status", "type", "c_tradingsymbol", "c_exchange", "o_price", "o_quantity"]
                for j, field in enumerate(fields):
                    if field.startswith("c_"):
                        value = item.get("condition", {}).get(field[2:], "")
                    elif field.startswith("o_"):
                        value = item.get("orders", [{}])[0].get(field[2:], "")
                    else:
                        value = item.get(field, "")
                    self.gtt_table.setItem(i, j, QTableWidgetItem(str(value)))
            self.update_status("GTT data refreshed.")
        except Exception as e:
            self.update_status(f"Error fetching GTT: {str(e)}")

    def save_gtt_to_excel(self):
        if not self.check_kite_initialized():
            return
        gtt = self.kite.get_gtts()
        if not isinstance(gtt, list):
            self.update_status(f"Invalid GTT data: Expected list, got {type(gtt)} - {gtt}")
            gtt = []
        df = pd.DataFrame(gtt)
        self.save_to_excel("GTT", df)

    def setup_margins_tab(self):
        self.margins_tab = QWidget()
        self.margins_layout = QVBoxLayout(self.margins_tab)

        description = self.create_description_label("Check your available and utilized funds (margins) for trading in the equity segment.")
        self.margins_layout.addWidget(description)

        self.margins_grid = QGridLayout()
        self.margins_labels = {}
        fields = ["net", "opening_balance", "live_balance", "holding_sales", "delivery"]
        for i, field in enumerate(fields):
            label = QLabel(f"{field.capitalize().replace('_', ' ')}:")
            value = QLabel("")
            self.margins_labels[field] = value
            self.margins_grid.addWidget(label, i, 0)
            self.margins_grid.addWidget(value, i, 1)
        self.margins_layout.addLayout(self.margins_grid)
        self.margins_refresh_btn = QPushButton("Refresh")
        self.margins_refresh_btn.clicked.connect(self.refresh_margins)
        self.margins_save_btn = QPushButton("Save to Excel")
        self.margins_save_btn.clicked.connect(lambda: self.save_margins_to_excel())
        self.margins_btn_layout = QHBoxLayout()
        self.margins_btn_layout.addWidget(self.margins_refresh_btn)
        self.margins_btn_layout.addWidget(self.margins_save_btn)
        self.margins_layout.addLayout(self.margins_btn_layout)
        self.tabs.addTab(self.margins_tab, "Margins")

    def refresh_margins(self):
        if not self.check_kite_initialized():
            return
        try:
            margins = self.kite.margins().get("equity", {})
            filtered_data = {
                "net": margins.get("net", ""),
                "opening_balance": margins.get("available", {}).get("opening_balance", ""),
                "live_balance": margins.get("available", {}).get("live_balance", ""),
                "holding_sales": margins.get("utilised", {}).get("holding_sales", ""),
                "delivery": margins.get("utilised", {}).get("delivery", "")
            }
            for key, value in filtered_data.items():
                self.margins_labels[key].setText(str(value))
            self.update_status("Margins data refreshed.")
        except Exception as e:
            self.update_status(f"Error fetching margins: {str(e)}")

    def save_margins_to_excel(self):
        if not self.check_kite_initialized():
            return
        margins = self.kite.margins().get("equity", {})
        filtered_data = {
            "net": margins.get("net", ""),
            "opening_balance": margins.get("available", {}).get("opening_balance", ""),
            "live_balance": margins.get("available", {}).get("live_balance", ""),
            "holding_sales": margins.get("utilised", {}).get("holding_sales", ""),
            "delivery": margins.get("utilised", {}).get("delivery", "")
        }
        df = pd.DataFrame([filtered_data])
        self.save_to_excel("Margins", df)

    def setup_historical_tab(self):
        self.historical_tab = QWidget()
        self.historical_layout = QVBoxLayout(self.historical_tab)

        description = self.create_description_label("Download historical OHLC (Open, High, Low, Close) data for stocks listed in 'e.xlsx'. First, generate the file, then start the download.")
        self.historical_layout.addWidget(description)

        self.start_date_layout = QHBoxLayout()
        self.start_date_label = QLabel("Start Date:")
        self.start_date_input = QDateEdit()
        self.start_date_input.setCalendarPopup(True)
        self.start_date_input.setDate(QDate(2025, 6, 1))
        self.start_date_layout.addWidget(self.start_date_label)
        self.start_date_layout.addWidget(self.start_date_input)
        self.historical_layout.addLayout(self.start_date_layout)
        self.end_date_layout = QHBoxLayout()
        self.end_date_label = QLabel("End Date:")
        self.end_date_input = QDateEdit()
        self.end_date_input.setCalendarPopup(True)
        self.end_date_input.setDate(QDate.currentDate())
        self.end_date_layout.addWidget(self.end_date_label)
        self.end_date_layout.addWidget(self.end_date_input)
        self.historical_layout.addLayout(self.end_date_layout)
        self.timeframe_layout = QHBoxLayout()
        self.timeframe_label = QLabel("Timeframe:")
        self.timeframe_input = QComboBox()
        self.timeframe_input.addItems(["day", "minute", "3minute", "5minute", "15minute", "30minute", "60minute"])
        self.timeframe_layout.addWidget(self.timeframe_label)
        self.timeframe_layout.addWidget(self.timeframe_input)
        self.historical_layout.addLayout(self.timeframe_layout)
        self.output_dir_layout = QHBoxLayout()
        self.output_dir_label = QLabel("Output Directory:")
        self.output_dir_input = QLineEdit()
        self.output_dir_input.setText(os.path.join(os.getcwd(), "Data"))
        self.output_dir_button = QPushButton("Browse")
        self.output_dir_button.clicked.connect(self.browse_output_dir)
        self.output_dir_layout.addWidget(self.output_dir_label)
        self.output_dir_layout.addWidget(self.output_dir_input)
        self.output_dir_layout.addWidget(self.output_dir_button)
        self.historical_layout.addLayout(self.output_dir_layout)
        self.button_control_layout = QHBoxLayout()
        self.download_btn = QPushButton("Start Download")
        self.stop_btn = QPushButton("Stop Download")
        self.generate_excel_btn = QPushButton("Generate e.xlsx")
        self.download_btn.clicked.connect(self.download_historical_data)
        self.stop_btn.clicked.connect(self.stop_download)
        self.generate_excel_btn.clicked.connect(self.generate_excel)
        self.stop_btn.setEnabled(False)
        self.button_control_layout.addWidget(self.download_btn)
        self.button_control_layout.addWidget(self.stop_btn)
        self.button_control_layout.addWidget(self.generate_excel_btn)
        self.historical_layout.addLayout(self.button_control_layout)
        self.worker = None
        self.tabs.addTab(self.historical_tab, "Historical Data")

    def browse_output_dir(self):
        dir_path = QFileDialog.getExistingDirectory(self, "Select Output Directory", os.getcwd())
        if dir_path:
            self.output_dir_input.setText(dir_path)

    def generate_excel(self):
        if not self.check_kite_initialized():
            return
        try:
            self.update_status("Generating e.xlsx...")

            def get_filtered_instruments(exchange):
                instruments_data = self.kite.instruments(exchange)
                filtered_instruments = [
                    {
                        'SYMBOL': inst['tradingsymbol'].replace('-BE', ''),
                        'TOKEN': inst['instrument_token'],
                        'name': inst['name'],
                        'segment': inst['segment'],
                    }
                    for inst in instruments_data
                    if inst['lot_size'] < 2
                    and "-X1" not in inst['tradingsymbol']
                    and "-P1" not in inst['tradingsymbol']
                    and "-RE" not in inst['tradingsymbol']
                    and "-W1" not in inst['tradingsymbol']
                    and "-IV" not in inst['tradingsymbol']
                    and "-BZ" not in inst['tradingsymbol']
                    and "-GS" not in inst['tradingsymbol']
                    and "-GB" not in inst['tradingsymbol']
                    and "-RR" not in inst['tradingsymbol']
                    and "-E1" not in inst['tradingsymbol']
                    and "-B1" not in inst['tradingsymbol']
                    and "-B" not in inst['tradingsymbol']
                    and "ANURASW1" not in inst['tradingsymbol']
                    and inst['name']
                    and "HDFCAMC" not in inst['name']
                    and "GOI LOAN" not in inst['name']
                    and "GOLDBOND" not in inst['name']
                    and "AXISAMC" not in inst['name']
                    and "BIRLASLAMC" not in inst['name']
                    and "DSPAMC" not in inst['name']
                    and "EDELAMC" not in inst['name']
                    and "ICICIPRAMC" not in inst['name']
                    and "KOTAKMAMC" not in inst['name']
                    and "LICNAMC" not in inst['name']
                    and "MIRAEAMC" not in inst['name']
                    and "MOTILALAMC" not in inst['name']
                    and "SBIAMC" not in inst['name']
                    and "TATAAML" not in inst['name']
                    and "UTIAMC" not in inst['name']
                    and "ZERODHAAMC" not in inst['name']
                    and "360ONEAMC" not in inst['name']
                    and "INAV" not in inst['name']
                    and "BSE" not in inst['name']
                    and (inst['name'] == "NIFTY 50" or "NIFTY" not in inst['name'])
                    and not re.match(r'^[0-9]', inst['name'])
                    and "SENSEX" not in inst['name']
                    and "FINANCIAL SERVICES" not in inst['name']
                    and "FRBGOI2035" not in inst['name']
                    and "NIPPON INDIA MUTUAL FUND" not in inst['name']
                    and "A-1" not in inst['name']
                    and "RELIANCE MUTUAL FUND" not in inst['name']
                    and not inst['name'].startswith('GS')
                    and not inst['name'].startswith('SOVEREIGN')
                    and not inst['name'].startswith('SGB')
                    and not inst['name'].startswith('NSE INDEX BHARATBOND')
                    and not inst['name'].startswith('DSP MUTUAL')
                    and not inst['name'].startswith('ADITYA BIRLA SUN LIFE MUTUAL')
                    and not inst['name'].startswith('AXIS MUTUAL FUND')
                    and not inst['name'].startswith('ICICI PRUDENTIAL')
                    and not inst['name'].startswith('KOTAK MAHINDRA MUTUAL')
                    and not inst['name'].startswith('MIRAE ASSET MUTUAL')
                    and not inst['name'].startswith('MOTILAL OSWAL MUTUAL')
                    and not inst['name'].startswith('NIPPON INDIA ETF')
                    and not inst['name'].startswith('SBI MUTUAL FUND')
                    and not inst['name'].startswith('TATA MUTUAL FUND')
                    and not inst['name'].startswith('SGB')
                    and not inst['name'].startswith('HDFC MUTUAL FUND')
                    and not inst['name'].startswith('UTI MUTUAL FUND')
                    and not inst['name'].startswith('SOVERIEGN GOLD')
                    and not inst['name'].startswith('SOVERIGN GOLD BOND')
                ]
                return filtered_instruments

            nse_instruments = get_filtered_instruments("NSE")
            bse_instruments = get_filtered_instruments("BSE")

            nse_df = pd.DataFrame(nse_instruments)
            bse_df = pd.DataFrame(bse_instruments)

            if nse_df.empty and bse_df.empty:
                self.update_status("No equity instruments found for NSE or BSE.")
                return

            if not nse_df.empty:
                nse_symbols = set(nse_df['SYMBOL'])
            else:
                nse_symbols = set()

            if not bse_df.empty:
                unique_bse_df = bse_df[~bse_df['SYMBOL'].isin(nse_symbols)]
            else:
                unique_bse_df = pd.DataFrame(columns=['SYMBOL', 'TOKEN'])

            combined_df = pd.concat([nse_df, unique_bse_df], ignore_index=True)

            base_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.getcwd()
            excel_path = os.path.join(os.getcwd(), "e.xlsx")
            with pd.ExcelWriter(excel_path, mode='w') as writer:
                combined_df.to_excel(writer, sheet_name='e', index=False)
            self.update_status(f"Generated e.xlsx with {len(combined_df)} symbols at {excel_path}")
            QMessageBox.information(self, "Success", "e.xlsx generated successfully!")
        except Exception as e:
            self.update_status(f"Error generating e.xlsx: {str(e)}")
            QMessageBox.critical(self, "Error", f"Failed to generate e.xlsx: {str(e)}")

    def download_historical_data(self):
        if not self.check_kite_initialized():
            return
        start_date = self.start_date_input.date().toPyDate()
        end_date = self.end_date_input.date().toPyDate()
        timeframe = self.timeframe_input.currentText()
        output_dir = self.output_dir_input.text()
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
        excel_path = os.path.join(os.getcwd(), "e.xlsx")
        if not os.path.exists(excel_path):
            self.update_status(f"Error: e.xlsx not found in {os.getcwd()}")
            QMessageBox.critical(self, "Error", "e.xlsx not found in the app directory!")
            return
        self.update_status("Starting historical data download...")
        self.download_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.generate_excel_btn.setEnabled(False)
        self.worker = DownloadWorker(self.kite, start_date, end_date, timeframe, output_dir, excel_path)
        self.worker.update_status.connect(self.update_status)
        self.worker.finished.connect(self.download_finished)
        self.worker.start()

    def stop_download(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.stop_btn.setEnabled(False)

    def download_finished(self):
        self.download_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.generate_excel_btn.setEnabled(True)
        if hasattr(self.worker, '_stop') and not self.worker._stop:
            QMessageBox.information(self, "Success", "Download completed successfully!")
        else:
            QMessageBox.information(self, "Stopped", "Download stopped by user.")
        self.worker = None

    # --- MODIFIED Stop Loss Tab ---
    def setup_stoploss_tab(self):
        self.stoploss_tab = QWidget()
        self.stoploss_layout = QVBoxLayout(self.stoploss_tab)

        description = self.create_description_label(
            "Automatically place GTT stop-loss orders. "
            "Choose a strategy, set a percentage if needed, select the assets, and run the analysis."
        )
        self.stoploss_layout.addWidget(description)

        # Main configuration grid for better alignment
        config_grid = QGridLayout()
        config_grid.setColumnStretch(0, 1)
        config_grid.setColumnStretch(1, 1)

        # --- Data Source Group ---
        source_group = QGroupBox("1. Select Asset Type")
        source_layout = QHBoxLayout()
        self.holdings_radio = QRadioButton("Holdings")
        self.positions_radio = QRadioButton("Positions")
        self.both_radio = QRadioButton("Both")
        self.both_radio.setChecked(True)
        source_layout.addWidget(self.holdings_radio)
        source_layout.addWidget(self.positions_radio)
        source_layout.addWidget(self.both_radio)
        source_group.setLayout(source_layout)
        config_grid.addWidget(source_group, 0, 0)

        # --- Strategy Group ---
        self.strategy_group = QGroupBox("2. Select Stop-Loss Strategy")
        strategy_v_layout = QVBoxLayout() # Vertical layout for strategy elements

        # Radio buttons for strategy type
        strategy_h_layout = QHBoxLayout()
        self.generic_radio = QRadioButton("Generic Stoploss")
        self.fixed_perc_radio = QRadioButton("Fixed % Stoploss")
        self.trailing_perc_radio = QRadioButton("Trailing % Stoploss")
        self.generic_radio.setChecked(True)
        strategy_h_layout.addWidget(self.generic_radio)
        strategy_h_layout.addWidget(self.fixed_perc_radio)
        strategy_h_layout.addWidget(self.trailing_perc_radio)
        strategy_v_layout.addLayout(strategy_h_layout)

        # Percentage input section
        perc_layout = QHBoxLayout()
        self.stoploss_perc_label = QLabel("Stop-Loss Percentage (%):")
        self.stoploss_perc_input = QDoubleSpinBox()
        self.stoploss_perc_input.setRange(-99.99, -0.01)  # Allow negative percentages
        self.stoploss_perc_input.setValue(-5.0)
        self.stoploss_perc_input.setDecimals(2)
        self.stoploss_perc_input.setEnabled(False)  # Disabled by default
        perc_layout.addWidget(self.stoploss_perc_label)
        perc_layout.addWidget(self.stoploss_perc_input)
        perc_layout.addStretch() # Pushes the input box to the left
        strategy_v_layout.addLayout(perc_layout)

        self.strategy_group.setLayout(strategy_v_layout)
        config_grid.addWidget(self.strategy_group, 0, 1)

        self.stoploss_layout.addLayout(config_grid)

        # Connect signals for enabling/disabling percentage input
        self.generic_radio.toggled.connect(lambda: self.stoploss_perc_input.setEnabled(False))
        self.fixed_perc_radio.toggled.connect(self.stoploss_perc_input.setEnabled)
        self.trailing_perc_radio.toggled.connect(self.stoploss_perc_input.setEnabled)

        # --- Table and Buttons ---
        self.stoploss_table = QTableWidget()
        self.stoploss_table.setColumnCount(9)
        self.stoploss_table.setHorizontalHeaderLabels(["Symbol", "Last Price", "Avg Price", "Highest High",
                                                       "Stop Loss Price", "Lower 4%", "Avg*0.98", "Avg*0.96", "Lowest Low"])
        self.stoploss_table.horizontalHeader().setStretchLastSection(True)
        self.stoploss_layout.addWidget(self.stoploss_table)

        self.stoploss_btn_layout = QHBoxLayout()
        self.stoploss_run_btn = QPushButton("Run Analysis")
        self.stoploss_stop_btn = QPushButton("Stop")
        self.stoploss_delete_btn = QPushButton("Delete GTTs")
        self.stoploss_run_btn.clicked.connect(self.run_stoploss_analysis)
        self.stoploss_stop_btn.clicked.connect(self.stop_stoploss)
        self.stoploss_delete_btn.clicked.connect(self.delete_gtt_orders)
        self.stoploss_stop_btn.setEnabled(False)
        self.stoploss_btn_layout.addWidget(self.stoploss_run_btn)
        self.stoploss_btn_layout.addWidget(self.stoploss_stop_btn)
        self.stoploss_btn_layout.addWidget(self.stoploss_delete_btn)
        self.stoploss_layout.addLayout(self.stoploss_btn_layout)

        self.stoploss_worker = None
        self.delete_worker = None
        self.tabs.addTab(self.stoploss_tab, "Stop Loss")

    def get_selected_stoploss_strategy(self):
        if self.generic_radio.isChecked():
            return "Generic Stoploss"
        elif self.fixed_perc_radio.isChecked():
            return "Fixed % Stoploss"
        elif self.trailing_perc_radio.isChecked():
            return "Trailing % Stoploss"
        return "Generic Stoploss"  # Fallback

    def get_selected_data_source(self):
        if self.holdings_radio.isChecked():
            return "Holdings"
        elif self.positions_radio.isChecked():
            return "Positions"
        elif self.both_radio.isChecked():
            return "Both"
        return "Both"  # Fallback

    def run_stoploss_analysis(self):
        if not self.check_kite_initialized():
            return

        strategy = self.get_selected_stoploss_strategy()
        data_source = self.get_selected_data_source()
        percentage = self.stoploss_perc_input.value()

        if strategy in ["Fixed % Stoploss", "Trailing % Stoploss"] and percentage >= 0:
            QMessageBox.warning(self, "Warning", "Percentage must be a negative value for Fixed or Trailing Stoploss.")
            return

        # CORRECTED LINE: Removed .emit() from the method call
        self.update_status(f"Starting analysis for '{data_source}' with strategy: '{strategy}' ({percentage}%)")

        self.stoploss_run_btn.setEnabled(False)
        self.stoploss_stop_btn.setEnabled(True)
        self.stoploss_delete_btn.setEnabled(False)

        self.stoploss_worker = StopLossWorker(self.kite, strategy, percentage, data_source)
        self.stoploss_worker.update_status.connect(self.update_status)
        self.stoploss_worker.data_ready.connect(self.update_stoploss_table)
        self.stoploss_worker.finished.connect(self.stoploss_finished)
        self.stoploss_worker.start()

    def stop_stoploss(self):
        if self.stoploss_worker and self.stoploss_worker.isRunning():
            self.stoploss_worker.stop()
            self.stoploss_stop_btn.setEnabled(False)

    def delete_gtt_orders(self):
        if not self.check_kite_initialized():
            return

        self.update_status("Starting GTT deletion...")
        self.stoploss_run_btn.setEnabled(False)
        self.stoploss_stop_btn.setEnabled(True)
        self.stoploss_delete_btn.setEnabled(False)

        self.delete_worker = DeleteGTTWorker(self.kite)
        self.delete_worker.update_status.connect(self.update_status)
        self.delete_worker.finished.connect(self.delete_gtt_finished)
        self.delete_worker.start()

    def update_stoploss_table(self, stoploss_data):
        self.stoploss_table.setRowCount(len(stoploss_data))
        for i, data in enumerate(stoploss_data):
            fields = ["tradingsymbol", "last_price", "average_price", "highest_high", "stop_loss_price",
                      "lower_4_percent", "avg_val*0.98", "avg_val*0.96", "lowest_low"]
            for j, field in enumerate(fields):
                self.stoploss_table.setItem(i, j, QTableWidgetItem(str(data.get(field, ""))))

    def stoploss_finished(self):
        self.stoploss_run_btn.setEnabled(True)
        self.stoploss_stop_btn.setEnabled(False)
        self.stoploss_delete_btn.setEnabled(True)
        if hasattr(self.stoploss_worker, '_stop') and not self.stoploss_worker._stop:
            QMessageBox.information(self, "Success", "Stop-loss processing completed successfully!")
        else:
            QMessageBox.information(self, "Stopped", "Stop-loss processing stopped by user.")
        self.stoploss_worker = None

    def delete_gtt_finished(self):
        self.stoploss_run_btn.setEnabled(True)
        self.stoploss_stop_btn.setEnabled(False)
        self.stoploss_delete_btn.setEnabled(True)
        if hasattr(self.delete_worker, '_stop') and not self.delete_worker._stop:
            QMessageBox.information(self, "Success", "All SELL GTT orders deleted successfully!")
        else:
            QMessageBox.information(self, "Stopped", "GTT deletion stopped by user.")
        self.delete_worker = None

    def setup_target_tab(self):
        self.target_tab = QWidget()
        self.target_layout = QVBoxLayout(self.target_tab)

        description = self.create_description_label("Place GTT buy orders for a list of stocks from an Excel file. Set a target based on past highs or a percentage gain.")
        self.target_layout.addWidget(description)

        self.target_strategy_group = QGroupBox("Target Strategy")
        self.target_strategy_layout = QHBoxLayout()
        self.max_high_radio = QRadioButton("Max Past Days")
        self.percentage_radio = QRadioButton("Percentage")
        self.max_high_radio.setChecked(True)
        self.target_strategy_layout.addWidget(self.max_high_radio)
        self.target_strategy_layout.addWidget(self.percentage_radio)
        self.target_strategy_group.setLayout(self.target_strategy_layout)
        self.target_layout.addWidget(self.target_strategy_group)

        self.excel_layout = QHBoxLayout()
        self.excel_label = QLabel("Stock List Excel:")
        self.excel_input = QLineEdit()
        self.excel_input.setReadOnly(True)
        self.excel_button = QPushButton("Browse")
        self.excel_button.clicked.connect(self.browse_excel_file)
        self.excel_layout.addWidget(self.excel_label)
        self.excel_layout.addWidget(self.excel_input)
        self.excel_layout.addWidget(self.excel_button)
        self.target_layout.addLayout(self.excel_layout)

        self.days_layout = QHBoxLayout()
        self.days_label = QLabel("Max Past Days:")
        self.days_input = QSpinBox()
        self.days_input.setRange(1, 365)
        self.days_input.setValue(30)
        self.days_layout.addWidget(self.days_label)
        self.days_layout.addWidget(self.days_input)
        self.target_layout.addLayout(self.days_layout)

        self.percentage_layout = QHBoxLayout()
        self.percentage_label = QLabel("Percentage Above Last Price (%):")
        self.percentage_input = QDoubleSpinBox()
        self.percentage_input.setRange(0.01, 100.0)
        self.percentage_input.setValue(5.0)
        self.percentage_input.setDecimals(2)
        self.percentage_layout.addWidget(self.percentage_label)
        self.percentage_layout.addWidget(self.percentage_input)
        self.target_layout.addLayout(self.percentage_layout)

        self.quantity_group = QGroupBox("Quantity Calculation")
        self.quantity_layout = QHBoxLayout()
        self.fixed_quantity_radio = QRadioButton("1 Unit")
        self.amount_based_radio = QRadioButton("By Amount:")
        self.fixed_quantity_radio.setChecked(True)
        self.target_amount_input = QDoubleSpinBox()
        self.target_amount_input.setRange(1.0, 10000000.0)
        self.target_amount_input.setValue(10000.0)
        self.target_amount_input.setDecimals(2)
        self.target_amount_input.setEnabled(False)
        self.amount_based_radio.toggled.connect(self.target_amount_input.setEnabled)

        self.quantity_layout.addWidget(self.fixed_quantity_radio)
        self.quantity_layout.addWidget(self.amount_based_radio)
        self.quantity_layout.addWidget(self.target_amount_input)
        self.quantity_group.setLayout(self.quantity_layout)
        self.target_layout.addWidget(self.quantity_group)

        self.target_table = QTableWidget()
        self.target_table.setColumnCount(2)
        self.target_table.setHorizontalHeaderLabels(["Symbol", "Token"])
        self.target_table.horizontalHeader().setStretchLastSection(True)
        self.target_layout.addWidget(self.target_table)

        self.target_btn_layout = QHBoxLayout()
        self.target_refresh_btn = QPushButton("Refresh Stock List")
        self.target_place_gtt_btn = QPushButton("Place GTT Orders")
        self.target_delete_all_gtt_btn = QPushButton("Delete All GTTs")
        self.target_stop_btn = QPushButton("Stop")
        self.target_refresh_btn.clicked.connect(self.refresh_target_table)
        self.target_place_gtt_btn.clicked.connect(self.place_gtt_orders)
        self.target_delete_all_gtt_btn.clicked.connect(self.delete_all_target_gtts)
        self.target_stop_btn.clicked.connect(self.stop_target_worker)
        self.target_stop_btn.setEnabled(False)
        self.target_btn_layout.addWidget(self.target_refresh_btn)
        self.target_btn_layout.addWidget(self.target_place_gtt_btn)
        self.target_btn_layout.addWidget(self.target_delete_all_gtt_btn)
        self.target_btn_layout.addWidget(self.target_stop_btn)
        self.target_layout.addLayout(self.target_btn_layout)

        self.target_worker = None
        self.delete_all_gtt_worker = None
        self.tabs.addTab(self.target_tab, "Target")

    def browse_excel_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Excel File", os.getcwd(), "Excel Files (*.xlsx *.xls)")
        if file_path:
            self.excel_input.setText(file_path)
            self.refresh_target_table()

    def refresh_target_table(self):
        if not self.check_kite_initialized():
            return
        excel_path = self.excel_input.text()
        if not excel_path or not os.path.exists(excel_path):
            self.update_status("Error: Please select a valid Excel file.")
            QMessageBox.warning(self, "Warning", "Please select a valid Excel file.")
            return
        try:
            nse_instruments = self.kite.instruments("NSE")
            instrument_map = {
                instrument['tradingsymbol']: instrument['instrument_token']
                for instrument in nse_instruments
            }
            stocks_df = pd.read_excel(excel_path)
            if 'SYMBOL' not in stocks_df.columns:
                self.update_status("Error: Excel file must contain 'SYMBOL' column.")
                QMessageBox.critical(self, "Error", "Excel file must contain 'SYMBOL' column.")
                return
            stocks_df['TOKEN'] = stocks_df['SYMBOL'].map(instrument_map)
            invalid_symbols = stocks_df[stocks_df['TOKEN'].isna()]['SYMBOL'].tolist()
            if invalid_symbols:
                self.update_status(f"Warning: Tokens not found for symbols: {', '.join(invalid_symbols)}")
            stocks_df = stocks_df.dropna(subset=['TOKEN'])
            self.target_table.setRowCount(len(stocks_df))
            for i, row in stocks_df.iterrows():
                self.target_table.setItem(i, 0, QTableWidgetItem(str(row['SYMBOL'])))
                self.target_table.setItem(i, 1, QTableWidgetItem(str(int(row['TOKEN']))))
            self.update_status(f"Loaded {len(stocks_df)} stocks from {excel_path}")
        except Exception as e:
            self.update_status(f"Error loading Excel file: {str(e)}")
            QMessageBox.critical(self, "Error", f"Failed to load Excel file: {str(e)}")

    def place_gtt_orders(self):
        if not self.check_kite_initialized():
            return
        excel_path = self.excel_input.text()
        if not excel_path or not os.path.exists(excel_path):
            self.update_status("Error: Please select a valid Excel file.")
            QMessageBox.warning(self, "Warning", "Please select a valid Excel file.")
            return
        days = self.days_input.value()
        strategy = "Max Past Days" if self.max_high_radio.isChecked() else "Percentage"
        percentage = self.percentage_input.value() if strategy == "Percentage" else 0.0

        quantity_strategy = "Amount" if self.amount_based_radio.isChecked() else "Fixed"
        quantity_amount = self.target_amount_input.value()

        self.update_status(f"Starting GTT order placement with strategy: {strategy}...")
        self.target_refresh_btn.setEnabled(False)
        self.target_place_gtt_btn.setEnabled(False)
        self.target_delete_all_gtt_btn.setEnabled(False)
        self.target_stop_btn.setEnabled(True)

        self.target_worker = TargetGTTWorker(self.kite, excel_path, days, strategy, percentage, quantity_strategy, quantity_amount)
        self.target_worker.update_status.connect(self.update_status)
        self.target_worker.finished.connect(self.gtt_placement_finished)
        self.target_worker.start()

    def stop_target_worker(self):
        if self.target_worker and self.target_worker.isRunning():
            self.target_worker.stop()
            self.update_status("Stopping GTT placement...")
        elif hasattr(self, 'delete_all_gtt_worker') and self.delete_all_gtt_worker and self.delete_all_gtt_worker.isRunning():
            self.delete_all_gtt_worker.stop()
            self.update_status("Stopping GTT deletion...")
        self.target_stop_btn.setEnabled(False)

    def gtt_placement_finished(self):
        self.target_refresh_btn.setEnabled(True)
        self.target_place_gtt_btn.setEnabled(True)
        self.target_delete_all_gtt_btn.setEnabled(True)
        self.target_stop_btn.setEnabled(False)
        if hasattr(self.target_worker, '_stop') and not self.target_worker._stop:
            QMessageBox.information(self, "Success", "GTT order placement completed successfully!")
        else:
            QMessageBox.information(self, "Stopped", "GTT order placement stopped by user.")
        self.target_worker = None

    def delete_all_target_gtts(self):
        if not self.check_kite_initialized():
            return

        reply = QMessageBox.question(self, 'Confirm Deletion',
                                     "Are you sure you want to delete ALL GTT orders (both BUY and SELL)?\nThis action cannot be undone.",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)

        if reply == QMessageBox.StandardButton.No:
            self.update_status("GTT deletion cancelled by user.")
            return

        self.update_status("Starting deletion of all GTT orders...")

        self.target_refresh_btn.setEnabled(False)
        self.target_place_gtt_btn.setEnabled(False)
        self.target_delete_all_gtt_btn.setEnabled(False)
        self.target_stop_btn.setEnabled(True)

        self.delete_all_gtt_worker = DeleteAllGTTsWorker(self.kite)
        self.delete_all_gtt_worker.update_status.connect(self.update_status)
        self.delete_all_gtt_worker.finished.connect(self.delete_all_target_gtts_finished)
        self.delete_all_gtt_worker.start()

    def delete_all_target_gtts_finished(self):
        self.target_refresh_btn.setEnabled(True)
        self.target_place_gtt_btn.setEnabled(True)
        self.target_delete_all_gtt_btn.setEnabled(True)
        self.target_stop_btn.setEnabled(False)

        if hasattr(self.delete_all_gtt_worker, '_stop') and not self.delete_all_gtt_worker._stop:
            QMessageBox.information(self, "Success", "Deletion of all GTT orders has completed.")
        else:
            QMessageBox.information(self, "Stopped", "GTT deletion was stopped by the user.")

        self.delete_all_gtt_worker = None

    def setup_fundamental_tab(self):
        self.fundamental_tab = QWidget()
        self.fundamental_layout = QVBoxLayout(self.fundamental_tab)

        description = self.create_description_label("Scrape fundamental data for a list of stocks from an input file. The tool fetches key ratios, growth metrics, and calculates a fundamental score, then saves the results to an output file.")
        self.fundamental_layout.addWidget(description)

        io_group = QGroupBox("File Configuration")
        io_layout = QGridLayout()

        io_layout.addWidget(QLabel("Input Stock List (CSV/Excel):"), 0, 0)
        self.fundamental_input_path_input = QLineEdit()
        self.fundamental_input_path_input.setReadOnly(True)
        io_layout.addWidget(self.fundamental_input_path_input, 0, 1)
        browse_input_btn = QPushButton("Browse...")
        browse_input_btn.clicked.connect(self.browse_fundamental_input_file)
        io_layout.addWidget(browse_input_btn, 0, 2)

        io_layout.addWidget(QLabel("Input Sheet Name:"), 1, 0)
        self.fundamental_input_sheet_input = QLineEdit("Sheet1")
        io_layout.addWidget(self.fundamental_input_sheet_input, 1, 1, 1, 2)

        io_group.setLayout(io_layout)
        self.fundamental_layout.addWidget(io_group)

        control_layout = QHBoxLayout()
        self.fundamental_start_btn = QPushButton("Start Scraping")
        self.fundamental_start_btn.clicked.connect(self.start_fundamental_scraping)
        self.fundamental_stop_btn = QPushButton("Stop")
        self.fundamental_stop_btn.clicked.connect(self.stop_fundamental_scraping)
        self.fundamental_stop_btn.setEnabled(False)
        control_layout.addWidget(self.fundamental_start_btn)
        control_layout.addWidget(self.fundamental_stop_btn)
        self.fundamental_layout.addLayout(control_layout)

        data_group = QGroupBox("Output Data Preview")
        data_layout = QVBoxLayout()

        refresh_btn = QPushButton("Refresh Data From Output File")
        refresh_btn.clicked.connect(self.refresh_fundamental_data)
        data_layout.addWidget(refresh_btn)

        self.fundamental_table = QTableWidget()
        self.fundamental_table.setSortingEnabled(True)
        data_layout.addWidget(self.fundamental_table)

        data_group.setLayout(data_layout)
        self.fundamental_layout.addWidget(data_group)

        self.tabs.addTab(self.fundamental_tab, "Fundamental")
        self.fundamental_worker = None
        if self.fundamental_input_path_input.text():
             self.refresh_fundamental_data()

    def browse_fundamental_input_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Input File", "", "Excel/CSV Files (*.xlsx *.csv);;All Files (*)")
        if path:
            self.fundamental_input_path_input.setText(path)
            self.save_config()
            self.refresh_fundamental_data()

    def start_fundamental_scraping(self):
        input_path = self.fundamental_input_path_input.text()
        if not input_path:
            QMessageBox.critical(self, "Error", "Please provide an input file path.")
            return

        try:
            base_name, _ = os.path.splitext(input_path)
            output_path = f"{base_name}_fundamental_data.xlsx"
            self.update_status(f"Output will be saved to: {os.path.basename(output_path)}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not create output file path: {e}")
            return

        self.fundamental_start_btn.setEnabled(False)
        self.fundamental_stop_btn.setEnabled(True)

        self.fundamental_worker = FundamentalScraperWorker(
            input_path=input_path,
            input_sheet=self.fundamental_input_sheet_input.text(),
            output_path=output_path,
            output_sheet="FundamentalData"
        )
        self.fundamental_worker.update_status.connect(self.update_status)
        self.fundamental_worker.data_ready.connect(self.update_fundamental_table)
        self.fundamental_worker.finished.connect(self.fundamental_scraping_finished)
        self.fundamental_worker.start()

    def stop_fundamental_scraping(self):
        if self.fundamental_worker and self.fundamental_worker.isRunning():
            self.fundamental_worker.stop()
            self.fundamental_stop_btn.setEnabled(False)

    def fundamental_scraping_finished(self):
        self.update_status("Fundamental scraping process has finished.")
        self.fundamental_start_btn.setEnabled(True)
        self.fundamental_stop_btn.setEnabled(False)
        if hasattr(self.fundamental_worker, '_stop') and not self.fundamental_worker._stop:
            QMessageBox.information(self, "Success", "Fundamental scraping completed successfully!")
        self.fundamental_worker = None
        self.refresh_fundamental_data()

    def update_fundamental_table(self, df):
        if df is None or df.empty:
            self.update_status("No data to display in the fundamental table.")
            self.fundamental_table.setRowCount(0)
            self.fundamental_table.setColumnCount(0)
            return

        self.update_status("Populating fundamental data preview...")
        self.fundamental_table.setRowCount(df.shape[0])
        self.fundamental_table.setColumnCount(df.shape[1])
        self.fundamental_table.setHorizontalHeaderLabels(df.columns)

        for i, row in enumerate(df.itertuples(index=False)):
            for j, value in enumerate(row):
                self.fundamental_table.setItem(i, j, QTableWidgetItem(str(value)))

        self.fundamental_table.resizeColumnsToContents()
        self.update_status("Data preview updated.")

    def refresh_fundamental_data(self):
        input_path = self.fundamental_input_path_input.text()
        if not input_path:
            self.update_status("Select an input file to see its corresponding output preview.")
            self.update_fundamental_table(None)
            return

        base_name, _ = os.path.splitext(input_path)
        output_path = f"{base_name}_fundamental_data.xlsx"

        if not os.path.exists(output_path):
            self.update_status(f"Output file '{os.path.basename(output_path)}' not found. Run scraper to generate it.")
            self.update_fundamental_table(None)
            return

        try:
            self.update_status(f"Reading data from '{os.path.basename(output_path)}' for preview...")
            df = pd.read_excel(output_path)
            self.update_fundamental_table(df)
        except Exception as e:
            self.update_status(f"Error refreshing fundamental data: {e}")
            self.update_fundamental_table(None)
            
    # +++ MODIFIED AITS TAB SETUP METHOD +++
    def setup_aits_tab(self):
        self.aits_tab = QWidget()
        self.aits_layout = QVBoxLayout(self.aits_tab)

        # --- Description ---
        description = self.create_description_label(
            "Automatic Intraday Trading System (AITS). Load a trade plan, configure settings, and run the bot."
        )
        self.aits_layout.addWidget(description)

        # --- Top Control Panel ---
        top_control_layout = QHBoxLayout()
        
        # File Input Group
        file_group = QGroupBox("Trade Plan")
        file_layout = QHBoxLayout()
        self.aits_file_input = QLineEdit()
        self.aits_file_input.setPlaceholderText("Select Excel trade file...")
        self.aits_file_input.setReadOnly(True)
        self.aits_load_btn = QPushButton("Load Trades")
        self.aits_load_btn.clicked.connect(self.load_aits_trade_file)
        file_layout.addWidget(self.aits_file_input)
        file_layout.addWidget(self.aits_load_btn)
        file_group.setLayout(file_layout)
        top_control_layout.addWidget(file_group)
        self.aits_layout.addLayout(top_control_layout)

        # --- Configuration Grids ---
        config_layout = QHBoxLayout()

        # Bot Configuration Group
        bot_config_group = QGroupBox("1. Bot Configuration")
        bot_config_grid = QGridLayout()
        
        bot_config_grid.addWidget(QLabel("Start Time:"), 0, 0)
        self.aits_start_time_input = QTimeEdit()
        self.aits_start_time_input.setDisplayFormat("HH:mm:ss")
        self.aits_start_time_input.setTime(QTime(9, 20, 0))
        bot_config_grid.addWidget(self.aits_start_time_input, 0, 1)

        bot_config_grid.addWidget(QLabel("End Time:"), 1, 0)
        self.aits_end_time_input = QTimeEdit()
        self.aits_end_time_input.setDisplayFormat("HH:mm:ss")
        self.aits_end_time_input.setTime(QTime(15, 10, 0))
        bot_config_grid.addWidget(self.aits_end_time_input, 1, 1)

        bot_config_grid.addWidget(QLabel("Polling Interval (s):"), 2, 0)
        self.aits_polling_input = QSpinBox()
        self.aits_polling_input.setRange(1, 60)
        self.aits_polling_input.setValue(1)
        bot_config_grid.addWidget(self.aits_polling_input, 2, 1)

        bot_config_grid.addWidget(QLabel("Square-off Before (min):"), 3, 0)
        self.aits_sbo_input = QSpinBox()
        self.aits_sbo_input.setRange(0, 30)
        self.aits_sbo_input.setValue(5)
        bot_config_grid.addWidget(self.aits_sbo_input, 3, 1)
        
        self.aits_paper_trading_checkbox = QCheckBox("Paper Trading Mode")
        self.aits_paper_trading_checkbox.setChecked(True)
        bot_config_grid.addWidget(self.aits_paper_trading_checkbox, 4, 0, 1, 2)


        bot_config_group.setLayout(bot_config_grid)
        config_layout.addWidget(bot_config_group)

        # Risk Management Group
        risk_group = QGroupBox("2. Risk Management")
        risk_layout = QGridLayout()
        risk_layout.addWidget(QLabel("Daily Loss Limit (₹):"), 0, 0)
        self.aits_loss_limit_input = QDoubleSpinBox()
        self.aits_loss_limit_input.setRange(-100000, -1)
        self.aits_loss_limit_input.setValue(-2500)
        risk_layout.addWidget(self.aits_loss_limit_input, 0, 1)
        
        risk_layout.addWidget(QLabel("Max Concurrent Trades:"), 1, 0)
        self.aits_max_trades_input = QSpinBox()
        self.aits_max_trades_input.setRange(1, 20)
        self.aits_max_trades_input.setValue(5)
        risk_layout.addWidget(self.aits_max_trades_input, 1, 1)
        risk_group.setLayout(risk_layout)
        config_layout.addWidget(risk_group)
        
        # Manual Trade Settings
        manual_trade_group = QGroupBox("3. Manual Trade Handling")
        manual_layout = QGridLayout()
        self.aits_adopt_manual_checkbox = QCheckBox("Adopt & manage manual trades")
        self.aits_adopt_manual_checkbox.setChecked(True)
        manual_layout.addWidget(self.aits_adopt_manual_checkbox, 0, 0, 1, 2)
        
        manual_layout.addWidget(QLabel("Default SL (%):"), 1, 0)
        self.aits_manual_sl_input = QDoubleSpinBox()
        self.aits_manual_sl_input.setRange(0.1, 10.0)
        self.aits_manual_sl_input.setValue(1.0)
        manual_layout.addWidget(self.aits_manual_sl_input, 1, 1)

        manual_layout.addWidget(QLabel("Default Target (%):"), 2, 0)
        self.aits_manual_target_input = QDoubleSpinBox()
        self.aits_manual_target_input.setRange(0.1, 20.0)
        self.aits_manual_target_input.setValue(1.5)
        manual_layout.addWidget(self.aits_manual_target_input, 2, 1)
        manual_trade_group.setLayout(manual_layout)
        config_layout.addWidget(manual_trade_group)

        self.aits_layout.addLayout(config_layout)


        # --- Main Controls ---
        main_control_group = QGroupBox("4. System Control")
        main_control_layout = QHBoxLayout()
        self.aits_start_btn = QPushButton("▶ Start Trading")
        self.aits_graceful_stop_btn = QPushButton("■ Stop Gracefully")
        self.aits_hard_stop_btn = QPushButton("🚨 SQUARE OFF ALL")
        
        self.aits_start_btn.clicked.connect(self.start_aits_worker)
        self.aits_graceful_stop_btn.clicked.connect(self.graceful_stop_aits_worker)
        self.aits_hard_stop_btn.clicked.connect(self.hard_stop_aits_worker)
        
        self.aits_graceful_stop_btn.setEnabled(False)
        self.aits_hard_stop_btn.setEnabled(False)
        
        main_control_layout.addWidget(self.aits_start_btn)
        main_control_layout.addWidget(self.aits_graceful_stop_btn)
        main_control_layout.addWidget(self.aits_hard_stop_btn)
        main_control_group.setLayout(main_control_layout)
        self.aits_layout.addWidget(main_control_group)

        # --- Dashboard and Logs ---
        dashboard_log_layout = QHBoxLayout()
        
        # Dashboard Table
        dashboard_group = QGroupBox("Live Dashboard")
        dashboard_v_layout = QVBoxLayout()
        self.aits_dashboard_table = QTableWidget()
        self.aits_dashboard_table.setColumnCount(7)
        self.aits_dashboard_table.setHorizontalHeaderLabels(["Symbol", "Action", "Qty", "Entry Price", "LTP", "Status", "Live P&L"])
        dashboard_v_layout.addWidget(self.aits_dashboard_table)
        dashboard_group.setLayout(dashboard_v_layout)
        dashboard_log_layout.addWidget(dashboard_group, 70)

        # Log Area
        log_group = QGroupBox("Logs")
        log_v_layout = QVBoxLayout()
        self.aits_log_area = QTextEdit()
        self.aits_log_area.setReadOnly(True)
        log_v_layout.addWidget(self.aits_log_area)
        log_group.setLayout(log_v_layout)
        dashboard_log_layout.addWidget(log_group, 30)

        self.aits_layout.addLayout(dashboard_log_layout)
        self.tabs.addTab(self.aits_tab, "AITS")
        
    def load_aits_trade_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Trade Plan File", "", "Excel Files (*.xlsx)")
        if file_path:
            self.aits_file_input.setText(file_path)
            self.aits_log_area.append(f"{datetime.now().strftime('%H:%M:%S')} - INFO: Loaded trade plan: {os.path.basename(file_path)}")

    def start_aits_worker(self):
        if not self.check_kite_initialized(): return
        if not self.aits_file_input.text():
            QMessageBox.warning(self, "Warning", "Please load a trade plan file first.")
            return
        
        try:
            user_id = self.kite.profile()["user_id"]
            enctoken = self.token_input.text().strip()
        except Exception as e:
            self.update_status(f"ERROR: Could not get profile for WebSocket: {e}")
            QMessageBox.critical(self, "Error", f"Could not get user profile. Please check your token and connection.\n\n{e}")
            return


        params = {
            "trade_file_path": self.aits_file_input.text(),
            "loss_limit": self.aits_loss_limit_input.value(),
            "max_trades": self.aits_max_trades_input.value(),
            "adopt_manual": self.aits_adopt_manual_checkbox.isChecked(),
            "manual_sl_perc": self.aits_manual_sl_input.value(),
            "manual_target_perc": self.aits_manual_target_input.value(),
            "start_time": self.aits_start_time_input.time(),
            "end_time": self.aits_end_time_input.time(),
            "polling_interval": self.aits_polling_input.value(),
            "square_off_buffer": self.aits_sbo_input.value(),
            "paper_trading": self.aits_paper_trading_checkbox.isChecked()
        }

        self.aits_worker = AITSWorker(self.kite, enctoken, user_id, params)
        self.aits_worker.log_update.connect(self.update_aits_log)
        self.aits_worker.dashboard_update.connect(self.update_aits_dashboard)
        self.aits_worker.finished.connect(self.aits_worker_finished)
        self.aits_worker.start()

        self.aits_start_btn.setEnabled(False)
        self.aits_graceful_stop_btn.setEnabled(True)
        self.aits_hard_stop_btn.setEnabled(True)
        self.aits_log_area.clear()
        self.update_aits_log("INFO: AITS Worker starting...")

    def graceful_stop_aits_worker(self):
        if self.aits_worker and self.aits_worker.isRunning():
            self.aits_worker.graceful_stop()
            self.aits_graceful_stop_btn.setText("Stopping Gracefully...")
            self.aits_graceful_stop_btn.setEnabled(False)

    def hard_stop_aits_worker(self):
        reply = QMessageBox.question(self, "Confirm Square Off", 
                                     "Are you sure you want to square off all open intraday positions and stop the bot?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            if self.aits_worker and self.aits_worker.isRunning():
                self.aits_worker.stop(hard_stop=True)
                self.aits_graceful_stop_btn.setEnabled(False)
                self.aits_hard_stop_btn.setText("Stopping...")
                self.aits_hard_stop_btn.setEnabled(False)

    def aits_worker_finished(self):
        self.update_aits_log("INFO: AITS Worker has finished.")
        self.aits_worker = None
        self.aits_start_btn.setEnabled(True)
        self.aits_graceful_stop_btn.setEnabled(False)
        self.aits_hard_stop_btn.setEnabled(False)
        self.aits_graceful_stop_btn.setText("■ Stop Gracefully")
        self.aits_hard_stop_btn.setText("🚨 SQUARE OFF ALL")

    def update_aits_log(self, message):
        self.aits_log_area.append(f"{datetime.now().strftime('%H:%M:%S')} - {message}")

    def update_aits_dashboard(self, trades_data):
        self.aits_dashboard_table.setRowCount(len(trades_data))
        for i, trade in enumerate(trades_data):
            self.aits_dashboard_table.setItem(i, 0, QTableWidgetItem(trade.get("symbol", "")))
            self.aits_dashboard_table.setItem(i, 1, QTableWidgetItem(trade.get("action", "")))
            self.aits_dashboard_table.setItem(i, 2, QTableWidgetItem(str(trade.get("quantity", 0))))
            self.aits_dashboard_table.setItem(i, 3, QTableWidgetItem(str(trade.get("entry_price", 0.0))))
            self.aits_dashboard_table.setItem(i, 4, QTableWidgetItem(str(trade.get("ltp", 0))))
            self.aits_dashboard_table.setItem(i, 5, QTableWidgetItem(trade.get("status", "")))
            self.aits_dashboard_table.setItem(i, 6, QTableWidgetItem(f"{trade.get('pnl', 0.0):.2f}"))
        self.aits_dashboard_table.resizeColumnsToContents()

    # +++ NEW TRADE PLAN TAB +++
    def setup_tradeplan_tab(self):
        self.tradeplan_tab = QWidget()
        layout = QVBoxLayout(self.tradeplan_tab)

        # Stock List Excel Input
        stock_group = QGroupBox("Stock List Excel (with SYMBOL column)")
        stock_layout = QHBoxLayout()
        self.tradeplan_stock_input = QLineEdit()
        self.tradeplan_stock_input.setPlaceholderText("Select Excel file...")
        self.tradeplan_stock_input.setReadOnly(True)
        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self.browse_tradeplan_stock_file)
        stock_layout.addWidget(self.tradeplan_stock_input)
        stock_layout.addWidget(browse_btn)
        stock_group.setLayout(stock_layout)
        layout.addWidget(stock_group)

        # Strategy Configurations
        strategy_group = QGroupBox("Trade Strategies")
        strategy_layout = QGridLayout()

        # Entry Price Strategy
        strategy_layout.addWidget(QLabel("Entry Strategy:"), 0, 0)
        self.tradeplan_entry_combo = QComboBox()
        self.tradeplan_entry_combo.addItems(["Range Breakout", "Moving Average Pullback"])
        strategy_layout.addWidget(self.tradeplan_entry_combo, 0, 1)
        self.tradeplan_entry_param_label = QLabel("Days:")
        strategy_layout.addWidget(self.tradeplan_entry_param_label, 0, 2)
        self.tradeplan_entry_param = QSpinBox()
        self.tradeplan_entry_param.setRange(5, 60)
        self.tradeplan_entry_param.setValue(15)
        self.tradeplan_entry_param.setSingleStep(5)
        strategy_layout.addWidget(self.tradeplan_entry_param, 0, 3)
        self.tradeplan_pullback_combo = QComboBox()
        self.tradeplan_pullback_combo.addItems(["9 EMA", "20 VWAP"])
        strategy_layout.addWidget(self.tradeplan_pullback_combo, 0, 3)
        
        self.tradeplan_entry_combo.currentTextChanged.connect(self._update_tradeplan_entry_ui)
        self._update_tradeplan_entry_ui()

        # Stop-Loss Strategy
        strategy_layout.addWidget(QLabel("Stop-Loss Strategy:"), 1, 0)
        self.tradeplan_sl_combo = QComboBox()
        self.tradeplan_sl_combo.addItems(["Swing Low", "Fixed %"])
        strategy_layout.addWidget(self.tradeplan_sl_combo, 1, 1)
        self.tradeplan_sl_param_label = QLabel("% Below Entry:")
        strategy_layout.addWidget(self.tradeplan_sl_param_label, 1, 2)
        self.tradeplan_sl_param = QDoubleSpinBox()
        self.tradeplan_sl_param.setRange(0.1, 10.0)
        self.tradeplan_sl_param.setValue(1.0)
        strategy_layout.addWidget(self.tradeplan_sl_param, 1, 3)

        self.tradeplan_sl_combo.currentTextChanged.connect(self._update_tradeplan_sl_ui)
        self._update_tradeplan_sl_ui()

        # Target Price Strategy
        strategy_layout.addWidget(QLabel("Target R:R Ratio:"), 2, 0)
        self.tradeplan_target_combo = QComboBox()
        self.tradeplan_target_combo.addItems(["1:1.5", "1:2", "1:3"])
        strategy_layout.addWidget(self.tradeplan_target_combo, 2, 1)

        # Quantity Strategy
        strategy_layout.addWidget(QLabel("Quantity Type:"), 3, 0)
        self.tradeplan_quantity_type_combo = QComboBox()
        self.tradeplan_quantity_type_combo.addItems(["CAPITAL", "RISK"])
        strategy_layout.addWidget(self.tradeplan_quantity_type_combo, 3, 1)
        strategy_layout.addWidget(QLabel("Value (₹):"), 3, 2)
        self.tradeplan_quantity_value = QDoubleSpinBox()
        self.tradeplan_quantity_value.setRange(100.0, 1000000.0)
        self.tradeplan_quantity_value.setValue(20000.0)
        strategy_layout.addWidget(self.tradeplan_quantity_value, 3, 3)

        strategy_group.setLayout(strategy_layout)
        layout.addWidget(strategy_group)

        # Generate Button
        generate_btn = QPushButton("Generate Trade Plan Excel")
        generate_btn.clicked.connect(self.generate_trade_plan)
        layout.addWidget(generate_btn)
        
        layout.addStretch() # Add spacer at the end

        self.tabs.addTab(self.tradeplan_tab, "TradePlan")

    def _update_tradeplan_entry_ui(self):
        if self.tradeplan_entry_combo.currentText() == "Range Breakout":
            self.tradeplan_entry_param_label.setText("Days:")
            self.tradeplan_entry_param_label.setVisible(True)
            self.tradeplan_entry_param.setVisible(True)
            self.tradeplan_pullback_combo.setVisible(False)
        else:
            self.tradeplan_entry_param_label.setVisible(False)
            self.tradeplan_entry_param.setVisible(False)
            self.tradeplan_pullback_combo.setVisible(True)

    def _update_tradeplan_sl_ui(self):
        visible = self.tradeplan_sl_combo.currentText() == "Fixed %"
        self.tradeplan_sl_param_label.setVisible(visible)
        self.tradeplan_sl_param.setVisible(visible)

    def browse_tradeplan_stock_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Stock List Excel", "", "Excel Files (*.xlsx *.xls)")
        if file_path:
            self.tradeplan_stock_input.setText(file_path)

    def generate_trade_plan(self):
        if not self.check_kite_initialized():
            return

        stock_file = self.tradeplan_stock_input.text()
        if not stock_file or not os.path.exists(stock_file):
            QMessageBox.warning(self, "Error", "Please select a valid stock list Excel file.")
            return

        token_file = "e.xlsx"
        if not os.path.exists(token_file):
            QMessageBox.warning(self, "Error", "e.xlsx file not found for token mapping. Please generate it from the 'Historical Data' tab first.")
            return

        self.update_status("Starting trade plan generation...")

        try:
            stocks_df = pd.read_excel(stock_file)
            if 'SYMBOL' not in stocks_df.columns:
                QMessageBox.critical(self, "Error", "Stock list must have a 'SYMBOL' column.")
                return

            tokens_df = pd.read_excel(token_file)
            token_map = dict(zip(tokens_df['SYMBOL'], tokens_df['TOKEN']))
        except Exception as e:
            self.update_status(f"Error reading Excel files: {e}")
            QMessageBox.critical(self, "File Error", f"Could not read the required Excel files: {e}")
            return


        trade_data = []
        today = datetime.now()
        from_date = today - timedelta(days=120)

        for index, row in stocks_df.iterrows():
            symbol = row['SYMBOL']
            token = token_map.get(symbol)
            if not token:
                self.update_status(f"Token not found for {symbol}, skipping.")
                continue
            
            self.update_status(f"({index+1}/{len(stocks_df)}) Processing {symbol}...")

            try:
                hist_data = self.kite.historical_data(
                    instrument_token=token,
                    from_date=from_date.strftime('%Y-%m-%d'),
                    to_date=today.strftime('%Y-%m-%d'),
                    interval="day",
                    continuous=False,
                    oi=False
                )
                df = pd.DataFrame(hist_data)
                if len(df) < 20: # Need at least 20 days for some calculations
                    self.update_status(f"Not enough historical data for {symbol} ({len(df)} days), skipping.")
                    continue

                df['date'] = pd.to_datetime(df['date'])
                df.sort_values('date', inplace=True)
            except Exception as e:
                self.update_status(f"Could not fetch historical data for {symbol}: {e}")
                continue

            # ENTRY_PRICE Calculation
            entry_strategy = self.tradeplan_entry_combo.currentText()
            if entry_strategy == "Range Breakout":
                days = self.tradeplan_entry_param.value()
                if len(df) < days:
                    continue
                recent_df = df.tail(days)
                max_high = recent_df.apply(lambda x: max(x['open'], x['close']), axis=1).max()
                entry_price = round(max_high * 1.001)
            else:  # Moving Average Pullback
                ma_type = self.tradeplan_pullback_combo.currentText()
                if ma_type == "9 EMA":
                    df['EMA9'] = df['close'].ewm(span=9, adjust=False).mean()
                    entry_price = round(df['EMA9'].iloc[-1])
                else:  # 20 VWAP approximation
                    df['VWAP_approx'] = (df['high'] + df['low'] + df['close'] * 2) / 4
                    df['VWAP20'] = df['VWAP_approx'].rolling(20).mean()
                    entry_price = round(df['VWAP20'].iloc[-1])

            # STOPLOSS_PRICE Calculation
            sl_strategy = self.tradeplan_sl_combo.currentText()
            if sl_strategy == "Swing Low":
                swing_low = df['low'].tail(5).min()
                stoploss_price = round(swing_low * 0.99)
            else:  # Fixed %
                perc = self.tradeplan_sl_param.value()
                stoploss_price = round(entry_price * (1 - perc / 100))

            # TARGET_PRICE Calculation
            rr_str = self.tradeplan_target_combo.currentText()
            rr = float(rr_str.split(':')[1])
            risk = entry_price - stoploss_price
            if risk <= 0:
                self.update_status(f"Calculated risk is zero or negative for {symbol}. Skipping.")
                continue
            target_price = round(entry_price + risk * rr)

            trade_data.append({
                'SYMBOL': symbol,
                'TOKEN': token,
                'ACTION': 'BUY',
                'ENTRY_TIME': '09:30:00',
                'ENTRY_PRICE': entry_price,
                'STOPLOSS_PRICE': stoploss_price,
                'TARGET_PRICE': target_price,
                'QUANTITY_TYPE': self.tradeplan_quantity_type_combo.currentText(),
                'QUANTITY_VALUE': self.tradeplan_quantity_value.value()
            })

        if trade_data:
            output_df = pd.DataFrame(trade_data)
            output_path = "trade_plan.xlsx"
            output_df.to_excel(output_path, index=False)
            self.update_status(f"Trade Plan generated successfully with {len(trade_data)} trades.")
            QMessageBox.information(self, "Success", f"Trade Plan generated and saved to {output_path}")
        else:
            self.update_status("No valid trades could be generated for the given stocks and strategies.")
            QMessageBox.warning(self, "Warning", "No valid trades could be generated.")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyleSheet("""
        QTabWidget::pane { border: 1px solid #cccccc; }
        QTabBar::tab { background: #e0e0e0; padding: 8px; }
        QTabBar::tab:selected { background: #4CAF50; color: white; }
        QPushButton { background-color: #4CAF50; color: white; padding: 5px; border-radius: 3px; font-weight: bold;}
        QPushButton:disabled { background-color: #cccccc; color: #666666; }
        QLineEdit, QComboBox, QDateEdit, QSpinBox, QDoubleSpinBox, QTimeEdit { border: 1px solid gray; padding: 3px; border-radius: 3px;}
        QTextEdit { background-color: #f0f0f0; }
        QTableWidget { border: 1px solid #cccccc; gridline-color: #e0e0e0; }
        QGroupBox { font-weight: bold; margin-top: 1ex; }
        QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 3px 0 3px; }
        QRadioButton, QCheckBox { padding: 3px; }
    """)
    window = StockApp()
    window.show()
    sys.exit(app.exec())
