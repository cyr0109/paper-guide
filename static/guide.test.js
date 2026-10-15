// 執行：node static/guide.test.js
const assert = require("assert");
const { richText } = require("./guide.js");

assert.strictEqual(richText("a\n\nb"), "<p>a</p><p>b</p>");
assert.strictEqual(richText("- x\n- y"), "<ul><li>x</li><li>y</li></ul>");
assert.strictEqual(richText("用 [[self-attention]] 算"),
  '<p>用 <span class="term" tabindex="0" data-term="self-attention">self-attention</span> 算</p>');
assert.strictEqual(richText("要注意：\n- x\n- y\n結尾"), "<p>要注意：</p><ul><li>x</li><li>y</li></ul><p>結尾</p>");
assert.strictEqual(richText("a\nb"), "<p>a<br>b</p>");
assert.strictEqual(richText("<script>"), "<p>&lt;script&gt;</p>");
assert.strictEqual(richText("[[<b>]]"), '<p><span class="term" tabindex="0" data-term="&lt;b&gt;">&lt;b&gt;</span></p>');
console.log("ok");
