;;; yomitan.el --- Yomitan dictionary lookup, on demand  -*- lexical-binding: t; -*-

;; Lookup against Yomitan dictionaries imported into sqlite by import.py.
;; Deliberately invoked: no idle timer, no hover, nothing happens until you
;; press a key -- the same way ispell is used here.
;;
;;   M-x yomitan-at-point     longest match starting at point
;;   M-x yomitan-lookup       prompt for a term
;;   M-x yomitan-dicts        what is imported
;;
;; Requires Emacs 29+ for built-in sqlite.

;;; Code:

(require 'sqlite)
(require 'cl-lib)

(defgroup yomitan nil "Yomitan dictionary lookup." :group 'applications)

(defcustom yomitan-db (expand-file-name "~/.local/share/yomitan/dict.db")
  "Path to the sqlite database built by import.py."
  :type 'file :group 'yomitan)

(defcustom yomitan-max-scan 12
  "Longest substring, in characters, tried when scanning forward from point.
Yomitan itself scans longest-first so that 位相幾何学 wins over 位相."
  :type 'integer :group 'yomitan)

(defcustom yomitan-max-entries 24
  "Most entries shown for one lookup."
  :type 'integer :group 'yomitan)

(defvar yomitan--conn nil)

(defun yomitan--db ()
  (unless (and yomitan--conn (sqlitep yomitan--conn))
    (unless (file-exists-p yomitan-db)
      (user-error "No Yomitan database at %s -- run import.py first" yomitan-db))
    (setq yomitan--conn (sqlite-open yomitan-db)))
  yomitan--conn)

(defun yomitan--rows (term)
  "Entries whose expression or reading is exactly TERM, best score first.
A character may be indexed under several readings (Unihan gives 會 wui6 and
wui5), so collapse rows that differ only by which reading matched."
  (let ((seen (make-hash-table :test #'equal)) (out '()))
    (dolist (r (sqlite-select
                (yomitan--db)
                (concat "SELECT t.expression, t.reading, t.plain, d.title"
                        " FROM term t JOIN dict d ON d.id = t.dict_id"
                        " WHERE t.expression = ?1 OR t.reading = ?1"
                        " ORDER BY t.score DESC, length(t.expression) LIMIT ?2")
                (list term (* 4 yomitan-max-entries))))
      (let ((key (list (nth 0 r) (nth 2 r) (nth 3 r))))
        (unless (gethash key seen)
          (puthash key t seen)
          (push r out))))
    (nreverse (seq-take (nreverse out) yomitan-max-entries))))

(defun yomitan--scan-at-point ()
  "Longest substring starting at point that is in the dictionary.
Returns (TERM . ROWS), or nil."
  (let* ((end (min (point-max) (+ (point) yomitan-max-scan)))
         (hit nil))
    (cl-loop for e downfrom end above (point)
             for term = (buffer-substring-no-properties (point) e)
             for rows = (and (> (length (string-trim term)) 0) (yomitan--rows term))
             when rows return (setq hit (cons term rows)))
    hit))

(defun yomitan--insert (term rows)
  (let ((inhibit-read-only t))
    (erase-buffer)
    (insert (propertize term 'face 'bold)
            (propertize (format "   %d %s\n\n" (length rows)
                                (if (= 1 (length rows)) "entry" "entries"))
                        'face 'shadow))
    (dolist (r rows)
      (pcase-let ((`(,expr ,read ,plain ,dict) r))
        (insert (propertize (or expr "") 'face '(:height 1.2 :weight bold)))
        (when (and read (not (string-empty-p read)) (not (equal read expr)))
          (insert (propertize (format " 【%s】" read) 'face 'font-lock-type-face)))
        (insert (propertize (format "   %s\n" (or dict "")) 'face 'shadow))
        (insert (or plain "") "\n\n")))
    (goto-char (point-min))))

(defun yomitan--show (term rows)
  (with-current-buffer (get-buffer-create "*yomitan*")
    (yomitan-mode)
    (yomitan--insert term rows)
    (display-buffer (current-buffer))))

;;;###autoload
(defun yomitan-lookup (term)
  "Look up TERM, prompting for it."
  (interactive
   (list (read-string "Yomitan: "
                      (when (use-region-p)
                        (buffer-substring-no-properties (region-beginning) (region-end))))))
  (let ((rows (yomitan--rows (string-trim term))))
    (if rows (yomitan--show term rows)
      (message "yomitan: no entry for %s" term))))

;;;###autoload
(defun yomitan-at-point ()
  "Look up the longest dictionary match starting at point."
  (interactive)
  (let ((hit (yomitan--scan-at-point)))
    (if hit (yomitan--show (car hit) (cdr hit))
      (message "yomitan: nothing at point"))))

;;;###autoload
(defun yomitan-dicts ()
  "Report the imported dictionaries."
  (interactive)
  (message "%s"
           (mapconcat
            (lambda (r) (format "%s  (%s entries, %s)" (nth 0 r) (nth 1 r) (nth 2 r)))
            (sqlite-select (yomitan--db)
                           (concat "SELECT d.title, COUNT(t.rowid), d.imported"
                                   " FROM dict d LEFT JOIN term t ON t.dict_id = d.id"
                                   " GROUP BY d.id"))
            "\n")))

(defvar-keymap yomitan-mode-map
  "q" #'quit-window
  "n" #'next-line
  "p" #'previous-line)

(define-derived-mode yomitan-mode special-mode "Yomitan"
  "Major mode for Yomitan dictionary results.")

(provide 'yomitan)
;;; yomitan.el ends here
