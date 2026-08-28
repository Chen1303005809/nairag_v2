import { Modal } from "antd";

import { ChildRevisionFullView } from "./ChildRevisionFullView";
import type { ReviewChildRevision, ReviewParentRevision } from "../api/types";

export function KnowledgeDetailModal({
  childRevision,
  parentRevision,
  parentName,
  open,
  onClose,
  title = "知识细则"
}: {
  childRevision: ReviewChildRevision | null;
  parentRevision?: ReviewParentRevision | null;
  parentName?: string;
  open: boolean;
  onClose: () => void;
  title?: string;
}): JSX.Element {
  return (
    <Modal open={open} title={title} onCancel={onClose} footer={null} destroyOnClose width={760}>
      {childRevision ? (
        <ChildRevisionFullView
          childRevision={childRevision}
          parentRevision={parentRevision}
          parentName={parentName}
        />
      ) : null}
    </Modal>
  );
}
