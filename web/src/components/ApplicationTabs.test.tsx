import { fireEvent, render, screen } from "@testing-library/react";
import { useEffect } from "react";
import { describe, expect, it, vi } from "vitest";

import { ApplicationTabs } from "./ApplicationTabs";

function Probe({ name, onUnmount }: { name: string; onUnmount: (name: string) => void }): JSX.Element {
  useEffect(() => () => onUnmount(name), [name, onUnmount]);
  return <div>{name}</div>;
}

describe("ApplicationTabs", () => {
  it("unmounts an inactive page so returning to it reloads its data", () => {
    const onUnmount = vi.fn();

    render(
      <ApplicationTabs
        defaultActiveKey="review"
        items={[
          {
            key: "review",
            label: "审核",
            children: <Probe name="审核" onUnmount={onUnmount} />
          },
          {
            key: "search",
            label: "检索",
            children: <Probe name="检索" onUnmount={onUnmount} />
          }
        ]}
      />
    );

    fireEvent.click(screen.getByRole("tab", { name: "检索" }));

    expect(onUnmount).toHaveBeenCalledWith("审核");
  });
});
