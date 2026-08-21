export type ConversationRole = "customer" | "ours";

export interface NormalizedMessage {
  speaker: string;
  role: ConversationRole;
  body: string;
  sent_at?: string | null;
}

export class ConversationParseError extends Error {}

export interface ConversationImageRecognition {
  text: string;
}

export interface PreparedConversation {
  messages: NormalizedMessage[];
  text: string;
  imageCount: number;
}

function configuredPositiveInteger(value: string | undefined, fallback: number): number {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback;
}

/**
 * These build-time limits mirror the backend defaults. Deployments that change
 * the backend limits should set the matching VITE_ variables so oversized
 * pasted conversations are rejected before a request is sent.
 */
export const conversationInputLimits = {
  maxMessages: configuredPositiveInteger(import.meta.env.VITE_LLM_MAX_CONVERSATION_MESSAGES, 200),
  maxChars: configuredPositiveInteger(import.meta.env.VITE_LLM_MAX_CONVERSATION_CHARS, 30_000)
};

const SPEAKER_TIMESTAMP_PATTERN =
  /^(.*?)[\s]+(?:(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}|\d{1,2}月\d{1,2}日)(?:[\s]+\d{1,2}:\d{2}(?::\d{2})?)?|\d{1,2}:\d{2}(?::\d{2})?)$/;

const IMAGE_PLACEHOLDER_PATTERN = /\[图片\]/g;
const SUPPORTED_IMAGE_TYPES = new Set(["image/png", "image/jpeg", "image/webp"]);

function normalizeSpeaker(rawSpeaker: string): string {
  return rawSpeaker.replace(/\s+/g, " ").trim();
}

function speakerRole(speaker: string): ConversationRole {
  return speaker.includes("融航") ? "ours" : "customer";
}

function parseSpeakerLine(line: string): { speaker: string; sentAt: string | null } | null {
  const timestampMatch = line.match(SPEAKER_TIMESTAMP_PATTERN);
  if (timestampMatch) {
    const speaker = normalizeSpeaker(timestampMatch[1]);
    if (speaker) {
      return { speaker, sentAt: null };
    }
  }
  const colonMatch = line.match(/^([^:：]{1,50})[:：]\s*(.+)$/);
  if (colonMatch) {
    const speaker = normalizeSpeaker(colonMatch[1]);
    if (speaker) {
      return { speaker, sentAt: null };
    }
  }
  return null;
}

/**
 * Parse WeCom-style copied chat text into normalized messages.
 *
 * Supported layouts:
 * - Blank-line separated blocks whose first line is "speaker [timestamp]";
 * - Single-line messages in "speaker: content" form.
 *
 * The WeCom forwarded-chat card emits dates such as "8-17 11:28". Speakers
 * whose display name contains 融航 (for example, "宋承臻(融航-咨询专员02)") are
 * treated as our staff; all other speakers are customers.
 */
export function parseWecomConversation(text: string): NormalizedMessage[] {
  const normalizedText = text.replace(/\r\n?/g, "\n").trim();
  if (!normalizedText) {
    throw new ConversationParseError("请先粘贴会话内容");
  }

  const blocks = normalizedText
    .split(/\n\s*\n+/)
    .map((block) => block.split("\n").map((line) => line.trim()).filter(Boolean))
    .filter((lines) => lines.length > 0);

  const messages: NormalizedMessage[] = [];
  for (const lines of blocks) {
    const speakerLine = parseSpeakerLine(lines[0]);
    if (speakerLine === null) {
      throw new ConversationParseError(
        `无法识别第 ${messages.length + 1} 条消息的说话人，请检查粘贴格式`
      );
    }
    if (lines.length === 1 && speakerLine.sentAt === null) {
      const colonMatch = lines[0].match(/^([^:：]{1,50})[:：]\s*(.+)$/);
      if (colonMatch) {
        messages.push({
          speaker: speakerLine.speaker,
          role: speakerRole(speakerLine.speaker),
          body: colonMatch[2].trim(),
          sent_at: null
        });
        continue;
      }
      throw new ConversationParseError(
        `第 ${messages.length + 1} 条消息缺少正文，请检查粘贴格式`
      );
    }
    const bodyLines = lines.slice(1).join("\n").trim();
    if (!bodyLines) {
      throw new ConversationParseError(
        `第 ${messages.length + 1} 条消息缺少正文，请检查粘贴格式`
      );
    }
    messages.push({
      speaker: speakerLine.speaker,
      role: speakerRole(speakerLine.speaker),
      body: bodyLines,
      sent_at: null
    });
  }

  if (messages.length === 0) {
    throw new ConversationParseError("未能从粘贴内容中解析出消息");
  }
  return messages;
}

export function assertBothPartiesPresent(messages: NormalizedMessage[]): void {
  const roles = new Set(messages.map((message) => message.role));
  if (!roles.has("customer") || !roles.has("ours")) {
    throw new ConversationParseError(
      "无法可靠识别客户与我方双方发言，请确认粘贴内容包含双方消息"
    );
  }
}

export function assertConversationWithinLimits(messages: NormalizedMessage[]): void {
  if (messages.length > conversationInputLimits.maxMessages) {
    throw new ConversationParseError(
      `会话消息数量超过上限（最多 ${conversationInputLimits.maxMessages} 条），请缩小粘贴范围`
    );
  }
  const totalChars = messages.reduce((total, message) => total + message.body.length, 0);
  if (totalChars > conversationInputLimits.maxChars) {
    throw new ConversationParseError(
      `会话文字长度超过上限（最多 ${conversationInputLimits.maxChars} 字符），请缩小粘贴范围`
    );
  }
}

function imagePlaceholderCount(value: string): number {
  return value.match(IMAGE_PLACEHOLDER_PATTERN)?.length ?? 0;
}

export function conversationImagePlaceholderCount(messages: NormalizedMessage[]): number {
  return messages.reduce((total, message) => total + imagePlaceholderCount(message.body), 0);
}

function replaceImagePlaceholders(value: string, recognizedTexts: string[]): string {
  let imageIndex = 0;
  return value.replace(IMAGE_PLACEHOLDER_PATTERN, () => recognizedTexts[imageIndex++] ?? "");
}

/**
 * Return image files carried by a browser paste operation. Native WeCom often
 * supplies them as files; browser-forwarded cards can instead expose data URLs
 * in their HTML clipboard representation, which is used as a fallback.
 */
export function conversationImagesFromClipboard(clipboardData: DataTransfer | null): File[] {
  const itemImages = Array.from(clipboardData?.items ?? [])
    .filter((item) => item.kind === "file" && (SUPPORTED_IMAGE_TYPES.has(item.type) || !item.type))
    .map((item) => item.getAsFile())
    .filter((file): file is File => file !== null);
  if (itemImages.length > 0) {
    return itemImages;
  }

  const fileImages = Array.from(clipboardData?.files ?? []).filter(
    (file) => SUPPORTED_IMAGE_TYPES.has(file.type) || !file.type
  );
  if (fileImages.length > 0) {
    return fileImages;
  }

  const html = clipboardData?.getData("text/html") ?? "";
  if (!html || typeof DOMParser === "undefined") {
    return [];
  }
  const document = new DOMParser().parseFromString(html, "text/html");
  const images = Array.from(document.querySelectorAll("img"));
  const explicitlyMarkedImages = images.filter((image) => image.alt.includes("图片"));
  const candidates = explicitlyMarkedImages.length > 0 ? explicitlyMarkedImages : images;
  return candidates
    .map((image, index) => imageFileFromDataUrl(image.src, index))
    .filter((file): file is File => file !== undefined);
}

function imageFileFromDataUrl(value: string, index: number): File | undefined {
  const match = /^data:(image\/(?:png|jpeg|webp));base64,([a-z0-9+/=\s]+)$/i.exec(value);
  if (!match) {
    return undefined;
  }
  try {
    const mediaType = match[1].toLowerCase();
    const binary = atob(match[2].replace(/\s/g, ""));
    const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
    const extension = mediaType === "image/jpeg" ? "jpg" : mediaType.slice("image/".length);
    return new File([bytes], `wecom-chat-image-${index + 1}.${extension}`, { type: mediaType });
  } catch {
    return undefined;
  }
}

/**
 * OCR every [图片] placeholder in encounter order and replace it before the
 * normalized conversation is sent to either fast-upload or fast-search.
 */
export async function prepareWecomConversation(
  text: string,
  images: Array<File | undefined>,
  recognizeImage: (image: File) => Promise<ConversationImageRecognition>
): Promise<PreparedConversation> {
  const messages = parseWecomConversation(text);
  const imageCount = conversationImagePlaceholderCount(messages);
  if (imageCount === 0) {
    return { messages, text, imageCount: 0 };
  }
  const missingImageIndexes = Array.from({ length: imageCount }, (_item, index) => index).filter(
    (index) => images[index] === undefined
  );
  if (missingImageIndexes.length > 0) {
    const missingDescription = missingImageIndexes
      .map((index) => `第 ${index + 1} 处`)
      .join("、");
    throw new ConversationParseError(
      `${missingDescription}“[图片]”尚未关联图片。请点击对应占位符后粘贴图片，或使用“添加/替换图片”选择文件。`
    );
  }

  const recognizedTexts: string[] = [];
  for (let imageIndex = 0; imageIndex < imageCount; imageIndex += 1) {
    const image = images[imageIndex];
    if (!image) {
      throw new ConversationParseError(`第 ${imageIndex + 1} 处“[图片]”尚未关联图片`);
    }
    const recognition = await recognizeImage(image);
    const recognizedText = recognition.text.trim();
    if (!recognizedText) {
      throw new ConversationParseError(`第 ${imageIndex + 1} 张聊天图片未识别出可用文字`);
    }
    recognizedTexts.push(recognizedText);
  }

  return {
    messages: replaceImagePlaceholdersInMessages(messages, recognizedTexts),
    text: replaceImagePlaceholders(text, recognizedTexts),
    imageCount
  };
}

function replaceImagePlaceholdersInMessages(
  messages: NormalizedMessage[],
  recognizedTexts: string[]
): NormalizedMessage[] {
  let imageIndex = 0;
  return messages.map((message) => ({
    ...message,
    body: message.body.replace(IMAGE_PLACEHOLDER_PATTERN, () => recognizedTexts[imageIndex++] ?? "")
  }));
}
