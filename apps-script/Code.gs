const OCR_EDIT_QUEUE_SHEET = '_OCR編集キュー';
const OCR_EDIT_HANDLER = 'handleOcrSheetEdit';

const OCR_EDIT_FIELDS = Object.freeze({
  '帳票一覧': Object.freeze({
    '個体識別番号': 'document.unifiedEarTag',
    '牛名': 'document.cattleName',
    '生年月日': 'document.birthDate',
    '登録番号': 'document.registrationNumber',
    '毛色': 'document.coatColor',
    '繁殖者': 'document.breeder',
    '所有者': 'document.owner',
    '父': 'pedigree.father',
    '母': 'pedigree.mother',
    '父方祖父': 'pedigree.paternalGrandfather',
    '父方祖母': 'pedigree.paternalGrandmother',
    '母方祖父': 'pedigree.maternalGrandfather',
    '母方祖母': 'pedigree.maternalGrandmother',
  }),
  '売却子牛一覧': Object.freeze({
    '個体識別番号': 'document.unifiedEarTag',
    '牛名': 'document.cattleName',
    '産次': '$record.parity',
    '日齢': '$record.ageDays',
    '体重kg': '$record.weightKg',
    '価格円': '$record.priceYen',
    '種雄牛名': '$record.sireName',
    '売却日': '$record.saleDate',
    '備考': '$record.notes',
  }),
  '繁殖履歴': Object.freeze({
    '個体識別番号': 'document.unifiedEarTag',
    '牛名': 'document.cattleName',
    '産歴番号': '$record.parity',
    '分娩年月日': '$record.calvingDate',
    '性別': '$record.sex',
    '分娩状況': '$record.outcome',
    '備考': '$record.notes',
  }),
});

const OCR_EDIT_QUEUE_HEADERS = Object.freeze([
  'editId', 'status', 'createdAt', 'jobId', 'recordId', 'fieldId',
  'previousValue', 'newValue', 'baseRevision', 'sourceSheet', 'sourceCell',
  'processedAt', 'error', 'appliedRevision',
]);

function setupOcrSheetEditing() {
  const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  let queue = spreadsheet.getSheetByName(OCR_EDIT_QUEUE_SHEET);
  if (!queue) {
    queue = spreadsheet.insertSheet(OCR_EDIT_QUEUE_SHEET);
  }
  queue.getRange(1, 1, 1, OCR_EDIT_QUEUE_HEADERS.length)
    .setValues([OCR_EDIT_QUEUE_HEADERS]);
  queue.hideSheet();

  ScriptApp.getProjectTriggers()
    .filter((trigger) => trigger.getHandlerFunction() === OCR_EDIT_HANDLER)
    .forEach((trigger) => ScriptApp.deleteTrigger(trigger));
  ScriptApp.newTrigger(OCR_EDIT_HANDLER)
    .forSpreadsheet(spreadsheet)
    .onEdit()
    .create();
}

function handleOcrSheetEdit(event) {
  if (!event || !event.range) return;
  const sourceSheet = event.range.getSheet();
  const fieldMap = OCR_EDIT_FIELDS[sourceSheet.getName()];
  if (!fieldMap || event.range.getRow() <= 1) return;

  const spreadsheet = sourceSheet.getParent();
  const queue = spreadsheet.getSheetByName(OCR_EDIT_QUEUE_SHEET);
  if (!queue) throw new Error('OCR編集キューがありません。setupOcrSheetEditingを実行してください。');

  const headers = sourceSheet
    .getRange(1, 1, 1, sourceSheet.getLastColumn())
    .getDisplayValues()[0];
  const hiddenJobColumn = headers.indexOf('__jobId') + 1;
  const legacyJobColumn = headers.indexOf('OCRジョブID') + 1;
  const jobColumn = hiddenJobColumn || legacyJobColumn;
  const revisionColumn = headers.indexOf('__revision') + 1;
  const recordColumn = headers.indexOf('__recordId') + 1;
  if (!jobColumn || !revisionColumn || !recordColumn) {
    throw new Error('OCR同期用メタデータ列がありません。アプリから再同期してください。');
  }

  const rows = [];
  for (let rowOffset = 0; rowOffset < event.range.getNumRows(); rowOffset += 1) {
    const row = event.range.getRow() + rowOffset;
    const jobId = sourceSheet.getRange(row, jobColumn).getDisplayValue().trim();
    const recordId = sourceSheet.getRange(row, recordColumn).getDisplayValue().trim();
    const baseRevision = Number(
      sourceSheet.getRange(row, revisionColumn).getDisplayValue(),
    );
    if (!jobId || !recordId || !Number.isInteger(baseRevision)) continue;

    for (let columnOffset = 0; columnOffset < event.range.getNumColumns(); columnOffset += 1) {
      const column = event.range.getColumn() + columnOffset;
      const mapped = fieldMap[headers[column - 1]];
      if (!mapped) continue;
      const fieldId = mapped.startsWith('$record.')
        ? `${recordId}.${mapped.substring('$record.'.length)}`
        : mapped;
      const editedCell = sourceSheet.getRange(row, column);
      const isSingleCell = event.range.getNumRows() === 1 && event.range.getNumColumns() === 1;
      rows.push([
        Utilities.getUuid(),
        'PENDING',
        new Date().toISOString(),
        jobId,
        fieldId.startsWith('document.') || fieldId.startsWith('pedigree.')
          ? 'document'
          : recordId,
        fieldId,
        isSingleCell && typeof event.oldValue !== 'undefined' ? String(event.oldValue) : '',
        editedCell.getDisplayValue(),
        baseRevision,
        sourceSheet.getName(),
        editedCell.getA1Notation(),
        '',
        '',
        '',
      ]);
    }
  }
  if (!rows.length) return;
  const lock = LockService.getDocumentLock();
  lock.waitLock(30000);
  try {
    queue.getRange(queue.getLastRow() + 1, 1, rows.length, OCR_EDIT_QUEUE_HEADERS.length)
      .setValues(rows);
  } finally {
    lock.releaseLock();
  }
}
