// AutoScout shared scouting notes endpoint. Deploy as a Google Apps Script web app.
const SPREADSHEET_ID = "PASTE_GOOGLE_SHEET_ID_HERE";
const SHEET_NAME = "AutoScout Notes";
const HEADERS = ["match", "time", "team", "phase", "text"];
function sheet_() {
  if (!SPREADSHEET_ID || SPREADSHEET_ID.startsWith("PASTE_")) throw new Error("Set SPREADSHEET_ID in Code.gs.");
  const book = SpreadsheetApp.openById(SPREADSHEET_ID);
  let sheet = book.getSheetByName(SHEET_NAME);
  if (!sheet) sheet = book.insertSheet(SHEET_NAME);
  if (!sheet.getLastRow()) sheet.appendRow(HEADERS);
  return sheet;
}
function readNotes_() {
  const values = sheet_().getDataRange().getDisplayValues();
  return values.slice(1).filter(row => row[4]).map(row => ({
    match: row[0], time: row[1], team: row[2], phase: row[3], text: row[4]
  }));
}
function doGet() {
  return ContentService.createTextOutput(JSON.stringify({notes: readNotes_()}))
    .setMimeType(ContentService.MimeType.JSON);
}
function doPost(e) {
  const payload = JSON.parse((e && e.postData && e.postData.contents) || "{}");
  const sheet = sheet_();
  const current = readNotes_();
  const keys = new Set(current.map(n => [n.match,n.time,n.team,n.phase,n.text].join("\u001f")));
  (payload.notes || []).forEach(note => {
    const row = [String(note.match || "General"), String(note.time || ""), String(note.team || "General"),
      String(note.phase || "Overall"), String(note.text || "")];
    const key = row.join("\u001f");
    if (row[4] && !keys.has(key)) { sheet.appendRow(row); keys.add(key); }
  });
  return ContentService.createTextOutput(JSON.stringify({notes: readNotes_()}))
    .setMimeType(ContentService.MimeType.JSON);
}
