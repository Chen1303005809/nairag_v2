import { describe, expect, it, vi } from "vitest";

import {
  assertBothPartiesPresent,
  assertConversationWithinLimits,
  ConversationParseError,
  parseWecomConversation,
  prepareWecomConversation
} from "./conversation";

describe("WeCom conversation adapter", () => {
  it("normalizes speaker blocks and applies the 融航 role rule", () => {
    const messages = parseWecomConversation(
      "张客户 2026-08-21 09:30\n登录一直失败怎么办？\n\n融航-李支持 09:31\n我先查询一下。"
    );

    expect(messages).toEqual([
      {
        speaker: "张客户",
        role: "customer",
        body: "登录一直失败怎么办？",
        sent_at: null
      },
      {
        speaker: "融航-李支持",
        role: "ours",
        body: "我先查询一下。",
        sent_at: null
      }
    ]);
    expect(() => assertBothPartiesPresent(messages)).not.toThrow();
  });

  it("rejects unrecognizable source text and oversized conversations", () => {
    expect(() => parseWecomConversation("没有说话人信息的聊天正文")).toThrow(
      ConversationParseError
    );
    const messages = Array.from({ length: 201 }, (_, index) => ({
      speaker: `客户${index}`,
      role: "customer" as const,
      body: "问题"
    }));
    expect(() => assertConversationWithinLimits(messages)).toThrow("数量超过上限");
  });

  it("parses a forwarded WeCom card with month-day timestamps and a staff suffix", () => {
    const messages = parseWecomConversation(
      "Edward 8-17 11:29\n为何会跳出资金账户不足呢\n\n宋承臻(融航-咨询专员02) 8-17 11:30\n这个我们反馈核实下"
    );

    expect(messages).toEqual([
      {
        speaker: "Edward",
        role: "customer",
        body: "为何会跳出资金账户不足呢",
        sent_at: null
      },
      {
        speaker: "宋承臻(融航-咨询专员02)",
        role: "ours",
        body: "这个我们反馈核实下",
        sent_at: null
      }
    ]);
    expect(() => assertBothPartiesPresent(messages)).not.toThrow();
  });

  it("OCRs forwarded-card images in order and replaces their placeholders", async () => {
    const firstImage = new File(["first"], "first.png", { type: "image/png" });
    const secondImage = new File(["second"], "second.png", { type: "image/png" });
    const recognizeImage = vi
      .fn()
      .mockResolvedValueOnce({ text: "资金账户可用余额不足" })
      .mockResolvedValueOnce({ text: "报单失败提示" });

    const prepared = await prepareWecomConversation(
      "Edward 8-17 11:28\n[图片]\n\nEdward 8-17 11:29\n[图片]\n\n宋承臻(融航-咨询专员02) 8-17 11:30\n我们核实下",
      [firstImage, secondImage],
      recognizeImage
    );

    expect(recognizeImage).toHaveBeenNthCalledWith(1, firstImage);
    expect(recognizeImage).toHaveBeenNthCalledWith(2, secondImage);
    expect(prepared.imageCount).toBe(2);
    expect(prepared.messages.map((message) => message.body)).toEqual([
      "资金账户可用余额不足",
      "报单失败提示",
      "我们核实下"
    ]);
    expect(prepared.text).not.toContain("[图片]");
  });

  it("does not submit image placeholders when the clipboard lacks their image data", async () => {
    await expect(
      prepareWecomConversation(
        "Edward 8-17 11:28\n[图片]",
        [],
        async () => ({ text: "unused" })
      )
    ).rejects.toThrow("第 1 处“[图片]”尚未关联图片");
  });
});
