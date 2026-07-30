# TradingView Signal Relay

FastAPI server that receives TradingView / Pine Script webhook alerts, forwards them to Telegram, and logs them to a CSV file.

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Liveness check |
| POST | `/webhook` | Accepts any JSON payload |
## Local Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python main.py
```
## Setup (Google Sheets)

If you are hosting on a platform like Render with ephemeral disks, you can send your signal data directly to Google Sheets:

1. Create a new Google Sheet.
2. In the top menu, go to **Extensions** $\rightarrow$ **Apps Script**.
3. Clear any existing code and paste the following script:

```javascript
function doPost(e) {
  try {
    var payload = JSON.parse(e.postData.contents);
    
    // Determine the sheet name from payload type or signal_type
    var typeName = payload.type || payload.signal_type || "Signals";
    typeName = String(typeName).trim();
    if (!typeName) {
      typeName = "Signals";
    }
    // Remove characters that are invalid in Google Sheet names: \ / ? * : [ ]
    typeName = typeName.replace(/[\\\/\?\*\:\[\]]/g, "_");
    // Strip leading/trailing single quotes
    typeName = typeName.replace(/^'+|'+$/g, "");
    if (typeName.length > 100) {
      typeName = typeName.substring(0, 100);
    }

    var activeSpreadsheet = SpreadsheetApp.getActiveSpreadsheet();
    var sheet = activeSpreadsheet.getSheetByName(typeName);
    if (!sheet) {
      sheet = activeSpreadsheet.insertSheet(typeName);
    }
    
    var headers = [
      "trade_id", "status", "received_at", "ticker", "timeframe", "side",
      "trigger_close", "entry_price", "sl", "tp_main", "tp1", "tp2", "tp3",
      "exit_price", "exit_reason", "exit_time", "telegram_sent", "telegram_error"
    ];
    
    if (sheet.getLastRow() === 0) {
      sheet.appendRow(headers);
    }
    
    var msgType = (payload.type || payload.signal_type || "").toUpperCase();
    var side = (payload.side || "").toUpperCase();
    var action = (payload.action || "").toLowerCase();
    var comment = payload.comment || "";
    var ticker = payload.ticker || payload.symbol || "";
    
    var isTrigger = msgType === "TRIGGER";
    var isEntry = side === "LONG" || action === "buy";
    var isExit = side === "EXIT" || action === "sell" || action === "close" || comment.indexOf("Sl") !== -1 || comment.indexOf("Tp") !== -1;
    
    if (isExit) {
      var data = sheet.getDataRange().getValues();
      var updated = false;
      for (var i = data.length - 1; i >= 1; i--) {
        var rowTicker = data[i][3];
        var rowStatus = data[i][1];
        if (rowTicker === ticker && rowStatus === "Open") {
          sheet.getRange(i + 1, 2).setValue("Closed");
          sheet.getRange(i + 1, 14).setValue(payload.price || payload.exit_price || "");
          sheet.getRange(i + 1, 15).setValue(comment || payload.reason || "Exit signal");
          sheet.getRange(i + 1, 16).setValue(Utilities.formatDate(new Date(), "GMT-4", "yyyy-MM-dd'T'HH:mm:ss.SSSXXX"));
          updated = true;
          break;
        }
      }
      
      if (!updated) {
        var rowData = new Array(headers.length).fill("");
        rowData[0] = payload.trade_id || "";
        rowData[1] = "Orphaned Exit";
        rowData[2] = Utilities.formatDate(new Date(), "GMT-4", "yyyy-MM-dd'T'HH:mm:ss.SSSXXX");
        rowData[3] = ticker;
        rowData[13] = payload.price || payload.exit_price || "";
        rowData[14] = comment || payload.reason || "Exit signal";
        rowData[15] = Utilities.formatDate(new Date(), "GMT-4", "yyyy-MM-dd'T'HH:mm:ss.SSSXXX");
        rowData[16] = String(payload.telegram_sent || "");
        rowData[17] = payload.telegram_error || "";
        sheet.appendRow(rowData);
      }
    } else {
      var status = isTrigger ? "Trigger" : (isEntry ? "Open" : "Unknown");
      var rowData = new Array(headers.length).fill("");
      rowData[0] = payload.trade_id || "";
      rowData[1] = status;
      rowData[2] = Utilities.formatDate(new Date(), "GMT-4", "yyyy-MM-dd'T'HH:mm:ss.SSSXXX");
      rowData[3] = ticker;
      rowData[4] = payload.timeframe || "";
      rowData[5] = payload.side || "";
      rowData[6] = payload.trigger_close || "";
      rowData[7] = payload.entry_price || payload.price || "";
      rowData[8] = payload.sl || "";
      rowData[9] = payload.tp_main || "";
      rowData[10] = payload.tp1 || "";
      rowData[11] = payload.tp2 || "";
      rowData[12] = payload.tp3 || "";
      rowData[16] = String(payload.telegram_sent || "");
      rowData[17] = payload.telegram_error || "";
      sheet.appendRow(rowData);
    }
    
    return ContentService.createTextOutput(JSON.stringify({status: "success"}))
      .setMimeType(ContentService.MimeType.JSON);
  } catch (err) {
    return ContentService.createTextOutput(JSON.stringify({status: "error", message: err.toString()}))
      .setMimeType(ContentService.MimeType.JSON);
  }
}
```

4. Click **Deploy** $\rightarrow$ **New deployment**.
5. Set **Select type** to **Web app**.
6. Set **Execute as** to **Me**.
7. Set **Who has access** to **Anyone**. (This is safe as long as the URL remains secret).
8. Click **Deploy** and authorize the script.
9. Copy the generated **Web app URL**.
10. Add `GOOGLE_SHEET_URL` to your env variables with this URL.




## Environment variables

| Variable | Default | Description |
|---|---|---|
| `WEBHOOK_TOKEN` | | Shared secret for auth (optional) |
| `TELEGRAM_BOT_TOKEN` | | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | | Target chat ID |
| `GOOGLE_SHEET_URL` | | Google Apps Script Web App URL to append logs |
| `HOST` | `0.0.0.0` | Bind address |
| `PORT` | `8000` | Bind port |
| `WORKERS` | `1` | Uvicorn workers (keep 1 for CSV locking) |
| `DATA_DIR` | `data` | Directory for CSV output |
| `CSV_PATH` | `data/signals.csv` | CSV file path |

## TradingView payload example

```json
{
  "token": "your-shared-secret",
  "ticker": "{{ticker}}",
  "timeframe": "{{interval}}",
  "side": "LONG",
  "entry_price": "{{close}}",
  "sl": "",
  "tp_main": ""
}
```