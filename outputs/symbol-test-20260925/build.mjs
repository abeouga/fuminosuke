import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const outputDir = process.cwd();
const workbook = Workbook.create();
const sheet = workbook.worksheets.add("設備点検票");
sheet.showGridLines = false;

const area = sheet.getRange("B2:D13");
area.format.font = { name: "Arial", size: 11, color: "#243746" };
area.format.verticalAlignment = "center";
sheet.getRange("A:A").format.columnWidth = 3;
sheet.getRange("B:B").format.columnWidth = 24;
sheet.getRange("C:C").format.columnWidth = 22;
sheet.getRange("D:D").format.columnWidth = 20;

sheet.mergeCells("B2:D2");
sheet.getRange("B2").values = [["青葉研修センター　設備点検票"]];
sheet.getRange("B2:D2").format.fill = "#173B53";
sheet.getRange("B2:D2").format.font = { name: "Arial", size: 16, bold: true, color: "#FFFFFF" };
sheet.getRange("B2:D2").format.rowHeight = 38;

sheet.mergeCells("B3:D3");
sheet.getRange("B3").values = [["模擬資料　｜　2026年9月　｜　管理番号 AOBA-TEST-001"]];
sheet.getRange("B3:D3").format.fill = "#E8F0F5";
sheet.getRange("B3:D3").format.font = { name: "Arial", size: 10, color: "#335166" };
sheet.getRange("B3:D3").format.rowHeight = 24;

sheet.getRange("B4").values = [["点検場所"]];
sheet.mergeCells("C4:D4");
sheet.getRange("C4").values = [["第2会議室・共用通路"]];
sheet.getRange("B4:D4").format.rowHeight = 28;
sheet.getRange("B4:D4").format.borders = { preset: "doubleBottom", style: "thin", color: "#94A8B5" };

sheet.getRange("B5:D5").values = [["点検項目", "判定記号", "確認内容"]];
sheet.getRange("B5:D5").format.fill = "#DCE8EE";
sheet.getRange("B5:D5").format.font = { name: "Arial", size: 11, bold: true, color: "#173B53" };
sheet.getRange("B5:D5").format.rowHeight = 28;
sheet.getRange("C5").format.horizontalAlignment = "center";

sheet.getRange("B6:B9").values = [["照明設備"], ["換気設備"], ["非常口表示"], ["備品の整理"]];
sheet.getRange("D6:D9").values = [["点灯と明るさ"], ["運転音・空気の流れ"], ["表示の視認性"], ["机・椅子の配置"]];
sheet.getRange("B6:D9").format.rowHeight = 62;
for (const row of [6, 7, 8, 9]) {
  sheet.getRange(`B${row}`).format.borders = { bottom: { style: "thin", color: "#C1D0D8" } };
  sheet.getRange(`D${row}`).format.borders = { bottom: { style: "thin", color: "#C1D0D8" } };
  const box = sheet.getRange(`C${row}`);
  box.format.fill = "#FFFFFF";
  box.format.borders = { preset: "outside", style: "medium", color: "#4E7085" };
}

sheet.mergeCells("B11:D11");
sheet.getRange("B11").values = [["記入方法：各判定欄に ○・◎・×・△ のいずれか1つを大きく記入してください。"]];
sheet.getRange("B11:D11").format.rowHeight = 30;
sheet.getRange("B11:D11").format.font = { name: "Arial", size: 10, color: "#335166" };

sheet.mergeCells("B13:D13");
sheet.getRange("B13").values = [["架空の点検票です。記号読み取り機能の確認に使用します。"]];
sheet.getRange("B13:D13").format.rowHeight = 24;
sheet.getRange("B13:D13").format.font = { name: "Arial", size: 10, color: "#687D89" };

workbook.recalculate();
const check = await workbook.inspect({
  kind: "region", sheetId: "設備点検票", range: "B2:D13", maxChars: 2500,
});
console.log(check.ndjson);
const preview = await workbook.render({
  sheetName: "設備点検票", range: "A1:E14", scale: 2, format: "png",
});
await fs.writeFile(path.join(outputDir, "preview.png"), new Uint8Array(await preview.arrayBuffer()));
const xlsx = await SpreadsheetFile.exportXlsx(workbook);
await xlsx.save(path.join(outputDir, "記号判定テスト用_設備点検票_A4.xlsx"));
