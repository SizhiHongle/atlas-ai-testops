"use client";

import { X } from "lucide-react";
import {
  useEffect,
  useRef,
  type ReactNode
} from "react";

import styles from "./dialog.module.css";

type DialogProps = {
  open: boolean;
  title: string;
  description: string;
  children: ReactNode;
  onClose: () => void;
};

export function Dialog({
  open,
  title,
  description,
  children,
  onClose
}: Readonly<DialogProps>) {
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    <dialog
      className={styles.dialog}
      ref={dialogRef}
      onClose={onClose}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
    >
      <header>
        <div>
          <p>ATLAS CONTROL</p>
          <h2>{title}</h2>
          <span>{description}</span>
        </div>
        <button type="button" onClick={onClose} aria-label="关闭">
          <X size={19} />
        </button>
      </header>
      {children}
    </dialog>
  );
}
