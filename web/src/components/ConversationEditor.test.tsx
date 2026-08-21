import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { createRef } from "react";
import { afterEach, describe, expect, it } from "vitest";

import { ConversationEditor } from "./ConversationEditor";
import type { ConversationEditorHandle } from "./ConversationEditor";

afterEach(() => {
  cleanup();
});

describe("ConversationEditor", () => {
  it("keeps each manual image attached to its own placeholder", () => {
    const ref = createRef<ConversationEditorHandle>();
    render(
      <ConversationEditor
        ref={ref}
        ariaLabel="测试聊天内容"
        placeholder="粘贴企业微信聊天记录"
      />
    );

    fireEvent.paste(screen.getByRole("textbox", { name: "测试聊天内容" }), {
      clipboardData: {
        getData: (type: string) =>
          type === "text/plain"
            ? "Edward 8-17 11:28\n[图片]\n\nEdward 8-17 11:29\n[图片]"
            : "",
        items: [],
        files: []
      }
    });

    const firstImage = new File(["first"], "first.png", { type: "image/png" });
    const secondImage = new File(["second"], "second.png", { type: "image/png" });
    const fileInput = screen.getByLabelText("选择聊天图片");

    fireEvent.click(
      screen.getAllByRole("button", { name: "图片占位符（点击后可粘贴或选择图片）" })[0]
    );
    fireEvent.change(fileInput, { target: { files: [firstImage] } });
    fireEvent.click(screen.getByRole("button", { name: "图片占位符（点击后可粘贴或选择图片）" }));
    fireEvent.change(fileInput, { target: { files: [secondImage] } });

    expect(ref.current?.getValue().images).toEqual([firstImage, secondImage]);

    fireEvent.click(screen.getByRole("button", { name: "已选择图片：first.png" }));
    fireEvent.keyDown(screen.getByRole("textbox", { name: "测试聊天内容" }), { key: "Delete" });

    const value = ref.current?.getValue();
    expect(value?.images).toEqual([secondImage]);
    expect(value?.text.match(/\[图片\]/g)).toHaveLength(1);
  });
});
