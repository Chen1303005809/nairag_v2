import { PictureOutlined } from "@ant-design/icons";
import { Button, Space, Typography } from "antd";
import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import type { ChangeEvent, ClipboardEvent, KeyboardEvent, MouseEvent } from "react";

import { conversationImagesFromClipboard } from "../conversation";

const IMAGE_PLACEHOLDER = "[图片]";
const IMAGE_PLACEHOLDER_SPLIT_PATTERN = /(\[图片\])/g;
const SUPPORTED_IMAGE_TYPES = new Set(["image/png", "image/jpeg", "image/webp"]);

interface ImageAttachment {
  file: File;
  objectUrl: string;
}

export interface ConversationEditorValue {
  text: string;
  /** One entry for every [图片] token, in visual/message order. */
  images: Array<File | undefined>;
}

export interface ConversationEditorHandle {
  getValue: () => ConversationEditorValue;
  replaceWithText: (text: string) => void;
  focus: () => void;
}

interface ConversationEditorProps {
  ariaLabel: string;
  disabled?: boolean;
  placeholder: string;
}

function isSupportedImage(file: File): boolean {
  return !file.type || SUPPORTED_IMAGE_TYPES.has(file.type);
}

function createObjectUrl(file: File): string {
  return typeof URL.createObjectURL === "function" ? URL.createObjectURL(file) : "";
}

function revokeObjectUrl(objectUrl: string): void {
  if (objectUrl && typeof URL.revokeObjectURL === "function") {
    URL.revokeObjectURL(objectUrl);
  }
}

function isImageToken(element: HTMLElement): boolean {
  return element.dataset.conversationImageId !== undefined;
}

function serializeEditor(root: HTMLElement): string {
  const serializeNode = (node: Node): string => {
    if (node.nodeType === Node.TEXT_NODE) {
      return node.textContent ?? "";
    }
    if (!(node instanceof HTMLElement)) {
      return "";
    }
    if (node.dataset.conversationImageId !== undefined) {
      return IMAGE_PLACEHOLDER;
    }
    if (node.tagName === "BR") {
      return "\n";
    }
    return Array.from(node.childNodes).map(serializeNode).join("");
  };

  let result = "";
  for (const child of Array.from(root.childNodes)) {
    const isBlock = child instanceof HTMLElement && /^(DIV|P|LI)$/.test(child.tagName);
    if (isBlock && result && !result.endsWith("\n")) {
      result += "\n";
    }
    result += serializeNode(child);
    if (isBlock && child.nextSibling && !result.endsWith("\n")) {
      result += "\n";
    }
  }
  return result.replace(/\n+$/, "");
}

function rangeInside(root: HTMLElement): Range | null {
  const selection = window.getSelection();
  if (!selection || selection.rangeCount === 0) {
    return null;
  }
  const range = selection.getRangeAt(0);
  return root.contains(range.commonAncestorContainer) ? range : null;
}

function moveCaretAfter(node: Node): void {
  const selection = window.getSelection();
  const document = node.ownerDocument;
  if (!selection || !document) {
    return;
  }
  const range = document.createRange();
  range.setStartAfter(node);
  range.collapse(true);
  selection.removeAllRanges();
  selection.addRange(range);
}

/**
 * A small native-contenteditable editor for WeCom cards. Image placeholders are
 * DOM tokens, so they retain an individual attachment rather than depending on
 * the changing order of a separate image array.
 */
export const ConversationEditor = forwardRef<ConversationEditorHandle, ConversationEditorProps>(
  function ConversationEditor({ ariaLabel, disabled = false, placeholder }, ref): JSX.Element {
    const editorRef = useRef<HTMLDivElement>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);
    const attachmentsRef = useRef<Map<string, ImageAttachment>>(new Map());
    const activeImageIdRef = useRef<string>();
    const idSequenceRef = useRef(0);
    const [imageStatus, setImageStatus] = useState({ total: 0, attached: 0 });
    const [, setActiveImageId] = useState<string>();

    const nextImageId = (): string => {
      idSequenceRef.current += 1;
      return `conversation-image-${idSequenceRef.current}`;
    };

    const imageElements = (): HTMLElement[] =>
      Array.from(editorRef.current?.querySelectorAll("[data-conversation-image-id]") ?? []).filter(
        (element): element is HTMLElement => element instanceof HTMLElement && isImageToken(element)
      );

    const imageElementById = (id: string): HTMLElement | undefined =>
      imageElements().find((element) => element.dataset.conversationImageId === id);

    const renderImageToken = (element: HTMLElement, id: string): void => {
      const attachment = attachmentsRef.current.get(id);
      element.className = "conversation-image-token";
      element.contentEditable = "false";
      element.tabIndex = 0;
      element.setAttribute("role", "button");
      element.setAttribute(
        "aria-label",
        attachment
          ? `已选择图片：${attachment.file.name}`
          : "图片占位符（点击后可粘贴或选择图片）"
      );
      element.title = attachment
        ? "已关联图片。点击后可替换，或使用右侧删除按钮移除该占位符。"
        : "点击选中后可粘贴图片，或使用“添加/替换图片”选择文件。";
      element.classList.toggle("is-selected", activeImageIdRef.current === id);
      element.replaceChildren();

      if (attachment) {
        const preview = element.ownerDocument.createElement("img");
        preview.className = "conversation-image-token-preview";
        preview.src = attachment.objectUrl;
        preview.alt = "聊天图片预览";
        element.append(preview);
        const filename = element.ownerDocument.createElement("span");
        filename.className = "conversation-image-token-label";
        filename.textContent = attachment.file.name;
        element.append(filename);
      } else {
        const label = element.ownerDocument.createElement("span");
        label.className = "conversation-image-token-label";
        label.textContent = IMAGE_PLACEHOLDER;
        element.append(label);
      }

      const remove = element.ownerDocument.createElement("button");
      remove.type = "button";
      remove.className = "conversation-image-token-remove";
      remove.dataset.conversationImageRemove = id;
      remove.setAttribute("aria-label", "删除此图片占位符");
      remove.title = "删除此图片占位符";
      remove.textContent = "×";
      element.append(remove);
    };

    const createImageToken = (id = nextImageId()): HTMLElement => {
      const root = editorRef.current;
      if (!root) {
        throw new Error("会话编辑器尚未初始化");
      }
      const token = root.ownerDocument.createElement("span");
      token.dataset.conversationImageId = id;
      renderImageToken(token, id);
      return token;
    };

    const clearAttachment = (id: string): void => {
      const attachment = attachmentsRef.current.get(id);
      if (attachment) {
        revokeObjectUrl(attachment.objectUrl);
        attachmentsRef.current.delete(id);
      }
    };

    const refreshImageStatus = (): void => {
      const tokens = imageElements();
      const presentIds = new Set(
        tokens.map((token) => token.dataset.conversationImageId).filter((id): id is string => Boolean(id))
      );
      for (const id of attachmentsRef.current.keys()) {
        if (!presentIds.has(id)) {
          clearAttachment(id);
        }
      }
      if (activeImageIdRef.current && !presentIds.has(activeImageIdRef.current)) {
        activeImageIdRef.current = undefined;
        setActiveImageId(undefined);
      }
      for (const token of tokens) {
        const id = token.dataset.conversationImageId;
        if (id) {
          renderImageToken(token, id);
        }
      }
      setImageStatus({
        total: tokens.length,
        attached: tokens.filter((token) => {
          const id = token.dataset.conversationImageId;
          return id !== undefined && attachmentsRef.current.has(id);
        }).length
      });
    };

    const selectImageToken = (id: string | undefined): void => {
      activeImageIdRef.current = id;
      setActiveImageId(id);
      for (const token of imageElements()) {
        const tokenId = token.dataset.conversationImageId;
        if (tokenId) {
          renderImageToken(token, tokenId);
        }
      }
    };

    const attachImage = (id: string, file: File): void => {
      clearAttachment(id);
      attachmentsRef.current.set(id, { file, objectUrl: createObjectUrl(file) });
      const token = imageElementById(id);
      if (token) {
        renderImageToken(token, id);
      }
    };

    const insertTextAtSelection = (text: string): string[] => {
      const root = editorRef.current;
      if (!root) {
        return [];
      }
      const placeholderIds: string[] = [];
      const fragment = root.ownerDocument.createDocumentFragment();
      let lastInsertedNode: Node | undefined;
      for (const part of text.split(IMAGE_PLACEHOLDER_SPLIT_PATTERN)) {
        if (!part) {
          continue;
        }
        const node = part === IMAGE_PLACEHOLDER ? createImageToken() : root.ownerDocument.createTextNode(part);
        if (part === IMAGE_PLACEHOLDER) {
          const id = (node as HTMLElement).dataset.conversationImageId;
          if (id) {
            placeholderIds.push(id);
          }
        }
        fragment.append(node);
        lastInsertedNode = node;
      }
      const range = rangeInside(root);
      if (range) {
        range.deleteContents();
        range.insertNode(fragment);
      } else {
        root.append(fragment);
      }
      if (lastInsertedNode) {
        moveCaretAfter(lastInsertedNode);
      }
      return placeholderIds;
    };

    const insertImagesAtSelection = (files: File[]): void => {
      const root = editorRef.current;
      if (!root || files.length === 0) {
        return;
      }
      const fragment = root.ownerDocument.createDocumentFragment();
      let lastInsertedToken: HTMLElement | undefined;
      for (const file of files) {
        const token = createImageToken();
        const id = token.dataset.conversationImageId;
        if (id) {
          attachImage(id, file);
        }
        fragment.append(token);
        lastInsertedToken = token;
      }
      const range = rangeInside(root);
      if (range) {
        range.deleteContents();
        range.insertNode(fragment);
      } else {
        root.append(fragment);
      }
      if (lastInsertedToken) {
        moveCaretAfter(lastInsertedToken);
      }
    };

    const applyImages = (files: File[], placeholderIds: string[] = []): void => {
      if (files.length === 0) {
        return;
      }
      let nextFileIndex = 0;
      for (const id of placeholderIds) {
        const file = files[nextFileIndex];
        if (!file) {
          break;
        }
        attachImage(id, file);
        nextFileIndex += 1;
      }
      if (nextFileIndex === 0 && activeImageIdRef.current) {
        const selectedToken = imageElementById(activeImageIdRef.current);
        if (selectedToken) {
          attachImage(activeImageIdRef.current, files[nextFileIndex]);
          nextFileIndex += 1;
        }
      }
      insertImagesAtSelection(files.slice(nextFileIndex));
      refreshImageStatus();
    };

    const replaceTextPlaceholdersWithTokens = (): void => {
      const root = editorRef.current;
      if (!root) {
        return;
      }
      const walker = root.ownerDocument.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      const textNodes: Text[] = [];
      let currentNode = walker.nextNode();
      while (currentNode) {
        if (!currentNode.parentElement?.closest("[data-conversation-image-id]")) {
          textNodes.push(currentNode as Text);
        }
        currentNode = walker.nextNode();
      }
      for (const textNode of textNodes) {
        if (!textNode.data.includes(IMAGE_PLACEHOLDER)) {
          continue;
        }
        const fragment = root.ownerDocument.createDocumentFragment();
        for (const part of textNode.data.split(IMAGE_PLACEHOLDER_SPLIT_PATTERN)) {
          if (part) {
            fragment.append(
              part === IMAGE_PLACEHOLDER ? createImageToken() : root.ownerDocument.createTextNode(part)
            );
          }
        }
        textNode.replaceWith(fragment);
      }
    };

    const removeImageToken = (id: string): void => {
      const token = imageElementById(id);
      if (!token) {
        return;
      }
      clearAttachment(id);
      token.remove();
      selectImageToken(activeImageIdRef.current === id ? undefined : activeImageIdRef.current);
      refreshImageStatus();
    };

    const replaceWithText = (text: string): void => {
      const root = editorRef.current;
      if (!root) {
        return;
      }
      for (const id of [...attachmentsRef.current.keys()]) {
        clearAttachment(id);
      }
      activeImageIdRef.current = undefined;
      setActiveImageId(undefined);
      root.replaceChildren();
      if (text) {
        insertTextAtSelection(text);
      }
      refreshImageStatus();
    };

    useImperativeHandle(
      ref,
      () => ({
        getValue: (): ConversationEditorValue => {
          const root = editorRef.current;
          if (!root) {
            return { text: "", images: [] };
          }
          replaceTextPlaceholdersWithTokens();
          refreshImageStatus();
          const tokens = imageElements();
          return {
            text: serializeEditor(root),
            images: tokens.map((token) => {
              const id = token.dataset.conversationImageId;
              return id ? attachmentsRef.current.get(id)?.file : undefined;
            })
          };
        },
        replaceWithText,
        focus: (): void => editorRef.current?.focus()
      }),
      []
    );

    useEffect(
      () => () => {
        for (const attachment of attachmentsRef.current.values()) {
          revokeObjectUrl(attachment.objectUrl);
        }
      },
      []
    );

    const handlePaste = (event: ClipboardEvent<HTMLDivElement>): void => {
      const pastedText = event.clipboardData.getData("text/plain");
      const pastedImages = conversationImagesFromClipboard(event.clipboardData).filter(isSupportedImage);
      if (!pastedText && pastedImages.length === 0) {
        return;
      }
      event.preventDefault();
      if (pastedText) {
        selectImageToken(undefined);
      }
      const placeholderIds = pastedText ? insertTextAtSelection(pastedText) : [];
      applyImages(pastedImages, placeholderIds);
      replaceTextPlaceholdersWithTokens();
      refreshImageStatus();
    };

    const handleInput = (): void => {
      replaceTextPlaceholdersWithTokens();
      refreshImageStatus();
    };

    const handleClick = (event: MouseEvent<HTMLDivElement>): void => {
      const target = event.target as HTMLElement;
      const remove = target.closest<HTMLElement>("[data-conversation-image-remove]");
      if (remove?.dataset.conversationImageRemove) {
        event.preventDefault();
        event.stopPropagation();
        removeImageToken(remove.dataset.conversationImageRemove);
        return;
      }
      const token = target.closest<HTMLElement>("[data-conversation-image-id]");
      if (token?.dataset.conversationImageId) {
        event.preventDefault();
        selectImageToken(token.dataset.conversationImageId);
        editorRef.current?.focus();
      } else {
        selectImageToken(undefined);
      }
    };

    const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => {
      const activeImageId = activeImageIdRef.current;
      if (!activeImageId || (event.key !== "Backspace" && event.key !== "Delete")) {
        return;
      }
      event.preventDefault();
      removeImageToken(activeImageId);
    };

    const chooseImage = (): void => {
      fileInputRef.current?.click();
    };

    const handleFileChange = (event: ChangeEvent<HTMLInputElement>): void => {
      const files = Array.from(event.target.files ?? []).filter(isSupportedImage);
      event.target.value = "";
      applyImages(files);
    };

    return (
      <Space direction="vertical" size={8} style={{ width: "100%" }}>
        <div
          ref={editorRef}
          aria-label={ariaLabel}
          aria-multiline="true"
          className="conversation-rich-editor"
          contentEditable={!disabled}
          data-placeholder={placeholder}
          onClick={handleClick}
          onInput={handleInput}
          onKeyDown={handleKeyDown}
          onPaste={handlePaste}
          role="textbox"
          suppressContentEditableWarning
        />
        <Space size={8} wrap>
          <Button disabled={disabled} icon={<PictureOutlined />} size="small" onClick={chooseImage}>
            添加/替换图片
          </Button>
          <Typography.Text type="secondary">
            {imageStatus.total === 0
              ? "粘贴卡片后，可点击“[图片]”再粘贴或选择对应图片。"
              : `已关联 ${imageStatus.attached}/${imageStatus.total} 张图片；删除占位符只会移除该图片。`}
          </Typography.Text>
        </Space>
        <input
          ref={fileInputRef}
          accept="image/png,image/jpeg,image/webp"
          aria-label="选择聊天图片"
          hidden
          multiple
          type="file"
          onChange={handleFileChange}
        />
      </Space>
    );
  }
);
