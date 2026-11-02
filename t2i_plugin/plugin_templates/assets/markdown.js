/* 正文 Markdown 渲染器（随模板发布，不依赖 CDN）
 *
 * 出图用的是本机 Chromium，服务器上通常没有外网；把 marked 这类库指向 CDN 时，取不到就是
 * 一片空白，而且失败是静默的。所以渲染器跟着模板走：插件渲染前会把 assets/xxx 内联成
 * data URI，离线也能用。
 *
 * 只覆盖消息里真会出现的写法：段落与单个换行（转成 <br>）、# ~ ###### 标题、
 * - / * / + 与 1. 列表（平铺）、> 引用、``` 代码块、| 表格 |、--- 分隔线；
 * 行内 `代码`、**粗体**、__粗体__、*斜体*、~~删除线~~、[文字](链接) 与裸链接。
 * 嵌套列表、脚注、内嵌 HTML 都不支持——日记正文用不到，多写只会把解析器写成半成品。
 *
 * 安全：先把原文整体做 HTML 转义，再回填自己生成的标签，消息里的 HTML 只会显示成文字。
 */
(function (global) {
  "use strict";

  var HTML_ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" };

  function escapeHtml(text) {
    return text.replace(/[&<>"]/g, function (ch) {
      return HTML_ESCAPES[ch];
    });
  }

  // 行内标记：行内代码先摘出来，里面的 ** / _ 不该再被当成标记
  function renderInline(raw) {
    var codes = [];
    var text = escapeHtml(raw).replace(/`([^`]+)`/g, function (_match, code) {
      codes.push(code);
      return "\u0000" + (codes.length - 1) + "\u0000";
    });

    text = text
      // 图片在离线出图里加载不出来，只留说明文字
      .replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, "$1")
      .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, '<a href="$2">$1</a>')
      .replace(/(^|[\s(（【])((?:https?:\/\/)[^\s<）)】]+)/g, '$1<a href="$2">$2</a>')
      .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
      .replace(/__([^_\n]+)__/g, "<strong>$1</strong>")
      .replace(/\*([^*\n]+)\*/g, "<em>$1</em>")
      .replace(/~~([^~\n]+)~~/g, "<del>$1</del>");

    return text.replace(/\u0000(\d+)\u0000/g, function (_match, index) {
      return "<code>" + codes[+index] + "</code>";
    });
  }

  // 同一段落里的换行按「硬换行」处理：原作者就是那么断的行，日记里不该被合并
  function renderLines(lines) {
    var parts = [];
    for (var i = 0; i < lines.length; i += 1) {
      parts.push(renderInline(lines[i]));
    }
    return parts.join("<br>");
  }

  function splitRow(line) {
    return line
      .replace(/^\s*\|/, "")
      .replace(/\|\s*$/, "")
      .split("|")
      .map(function (cell) {
        return cell.trim();
      });
  }

  // 表格分隔行 |---|---|；正文里偶尔出现的竖线不该被误判成表格
  function isTableRule(line) {
    return typeof line === "string" && /^\s*\|?[\s:|-]*-[\s:|-]*\|[\s:|-]*$/.test(line);
  }

  function isBlockStart(lines, index) {
    var line = lines[index];
    return (
      /^\s*```/.test(line) ||
      /^\s{0,3}#{1,6}\s+/.test(line) ||
      /^\s{0,3}([-*_])(\s*\1){2,}\s*$/.test(line) ||
      /^\s*>/.test(line) ||
      /^\s*([-*+]|\d+[.)])\s+/.test(line) ||
      (line.indexOf("|") >= 0 && isTableRule(lines[index + 1]))
    );
  }

  function renderBlocks(lines) {
    var html = "";
    var index = 0;
    var match;
    var line;

    while (index < lines.length) {
      line = lines[index];
      if (!line.trim()) {
        index += 1;
        continue;
      }

      // 围栏代码块：里面的空行、缩进都要原样留住
      if (/^\s*```/.test(line)) {
        var code = [];
        index += 1;
        while (index < lines.length && !/^\s*```\s*$/.test(lines[index])) {
          code.push(lines[index]);
          index += 1;
        }
        index += 1; // 跳过收尾围栏；没有收尾就吃到文末
        html += "<pre><code>" + escapeHtml(code.join("\n")) + "</code></pre>";
        continue;
      }

      match = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/.exec(line);
      if (match) {
        var level = match[1].length;
        html += "<h" + level + ">" + renderInline(match[2]) + "</h" + level + ">";
        index += 1;
        continue;
      }

      if (/^\s{0,3}([-*_])(\s*\1){2,}\s*$/.test(line)) {
        html += "<hr>";
        index += 1;
        continue;
      }

      if (/^\s*>/.test(line)) {
        var quoted = [];
        while (index < lines.length && /^\s*>/.test(lines[index])) {
          quoted.push(lines[index].replace(/^\s*>\s?/, ""));
          index += 1;
        }
        html += "<blockquote>" + renderLines(quoted) + "</blockquote>";
        continue;
      }

      match = /^\s*([-*+]|\d+[.)])\s+(.*)$/.exec(line);
      if (match) {
        var ordered = /\d/.test(match[1]);
        var items = "";
        while (index < lines.length) {
          var item = /^\s*([-*+]|\d+[.)])\s+(.*)$/.exec(lines[index]);
          if (!item || /\d/.test(item[1]) !== ordered) {
            break;
          }
          items += "<li>" + renderInline(item[2]) + "</li>";
          index += 1;
        }
        html += ordered ? "<ol>" + items + "</ol>" : "<ul>" + items + "</ul>";
        continue;
      }

      if (line.indexOf("|") >= 0 && isTableRule(lines[index + 1])) {
        var head = splitRow(line);
        var rows = "";
        index += 2;
        while (index < lines.length && lines[index].trim() && lines[index].indexOf("|") >= 0) {
          var cells = splitRow(lines[index]);
          var htmlCells = "";
          for (var c = 0; c < head.length; c += 1) {
            htmlCells += "<td>" + renderInline(cells[c] || "") + "</td>";
          }
          rows += "<tr>" + htmlCells + "</tr>";
          index += 1;
        }
        var htmlHead = "";
        for (var h = 0; h < head.length; h += 1) {
          htmlHead += "<th>" + renderInline(head[h]) + "</th>";
        }
        html +=
          "<table><thead><tr>" + htmlHead + "</tr></thead><tbody>" + rows + "</tbody></table>";
        continue;
      }

      var paragraph = [];
      while (index < lines.length && lines[index].trim() && !isBlockStart(lines, index)) {
        paragraph.push(lines[index]);
        index += 1;
      }
      html += "<p>" + renderLines(paragraph) + "</p>";
    }

    return html;
  }

  global.t2iMarkdown = function (source) {
    var text = String(source == null ? "" : source)
      .replace(/\r\n?/g, "\n")
      .replace(/^\n+/, "")
      .replace(/\s+$/, "");
    if (!text) {
      return "";
    }
    return renderBlocks(text.split("\n"));
  };
})(window);
