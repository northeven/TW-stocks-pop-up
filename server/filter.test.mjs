import { test } from 'node:test';
import assert from 'node:assert/strict';
import { screen } from './filter.js';

// 應該被擋（廣告／聯絡方式／詐騙話術），含常見拆字與全形規避
const SHOULD_BLOCK = [
  'https://reurl.cc/abc123 快來看',
  '看這裡 www.spam-site.xyz',
  '飆股明牌 加賴 stockteacher',
  '加 賴 ｜ a b c 1 2 3',                 // 分隔符拆字
  '徵賴 line id: money888',
  '加ＬＩＮＥ　ＭＯＮＥＹ',                 // 全形
  '想賺錢加微信 vx666888',
  'ｖ　ｘ： 888888',                       // 全形 + 拆字 vx
  '飛機群 @cryptopump99',
  '聯絡 0912345678 帶你操作',
  '穩賺不賠 保證獲利 老師帶單',
  '娛樂城送體驗金 百家樂',
];

// 不該被擋（正常台股討論與情緒用語，避免誤殺）
const SHOULD_PASS = [
  '台積電噴出啦 2330 衝1200',
  '大盤破兩萬三 嗨爆',
  '這根長黑 韭菜哭哭',
  '軋空啦 空軍投降',
  '幹 我當沖又被巴',                        // 情緒用語，非攻擊
  '梭哈了 all in 台積電',
  '17000 點會守住嗎',
  '賴清德政策利多？',                        // 姓「賴」＋無聯絡意圖
  '這檔本益比才 12 倍',
  'TSM ADR 漲 3%',
];

test('攔截廣告／聯絡方式／詐騙', () => {
  for (const t of SHOULD_BLOCK) {
    assert.equal(screen(t).ok, false, `應被擋卻放行：「${t}」`);
  }
});

test('放行正常台股討論（不誤殺）', () => {
  for (const t of SHOULD_PASS) {
    assert.equal(screen(t).ok, true, `不該擋卻攔下：「${t}」`);
  }
});
