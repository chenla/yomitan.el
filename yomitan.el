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
(require 'url-util)

(defconst yomitan-version "0.6"
  "Bumped on every behaviour change, because the autoload loads this file once
and an Emacs that has already loaded it keeps the old definitions until
\\[load-library].  `yomitan-dicts' reports it, so a surprising result can be
checked against the source before being treated as a bug.")

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

(defcustom yomitan-dictionary-priority
  '("CC-Canto" "Unihan" "CC-CEDICT" "Jitendex")
  "Dictionary titles, best first, matched as substrings.

Scores are not comparable across dictionaries -- Jitendex carries JMdict
frequency ranks, while the others are flat -- so sorting by score alone lets
Japanese outrank Cantonese on a Han character.  Reorder this to taste; a
dictionary not listed sorts after every listed one, by its own score."
  :type '(repeat string) :group 'yomitan)

(defcustom yomitan-wiktionary-url "https://en.wiktionary.org/wiki/%s"
  "Wiktionary URL template; %s is the percent-encoded headword."
  :type 'string :group 'yomitan)

(defcustom yomitan-wiktionary-browser #'eww
  "How to open Wiktionary.  `eww' keeps it in Emacs; `browse-url' leaves."
  :type '(choice (const :tag "eww, inside Emacs" eww)
                 (const :tag "external browser" browse-url)
                 function)
  :group 'yomitan)

(defvar yomitan--conn nil)
(defvar-local yomitan--term nil "Headword this *yomitan* buffer is showing.")
(defvar-local yomitan--lang nil "Source language of the best entry shown.")

(defun yomitan--db ()
  (unless (and yomitan--conn (sqlitep yomitan--conn))
    (unless (file-exists-p yomitan-db)
      (user-error "No Yomitan database at %s -- run import.py first" yomitan-db))
    (setq yomitan--conn (sqlite-open yomitan-db)))
  yomitan--conn)

(defun yomitan--dict-rank (title)
  (or (seq-position yomitan-dictionary-priority title
                    (lambda (pat d) (string-match-p (regexp-quote pat) (or d ""))))
      (length yomitan-dictionary-priority)))

(defun yomitan--rows (term)
  "Entries whose expression or reading is exactly TERM.
Ordered by `yomitan-dictionary-priority', then by the dictionary\'s own score.
A character may be indexed under several readings (Unihan gives 會 wui6 and
wui5), so collapse rows that differ only by which reading matched."
  (let ((seen (make-hash-table :test #'equal)) (out '()))
    (dolist (r (sqlite-select
                (yomitan--db)
                (concat "SELECT t.expression, t.reading, t.plain, d.title, t.score,"
                        " d.src_lang"
                        " FROM term t JOIN dict d ON d.id = t.dict_id"
                        " WHERE t.expression = ?1 OR t.reading = ?1"
                        " ORDER BY t.score DESC, length(t.expression) LIMIT ?2")
                (list term (* 8 yomitan-max-entries))))
      (let ((key (list (nth 0 r) (nth 2 r) (nth 3 r))))
        (unless (gethash key seen)
          (puthash key t seen)
          (push r out))))
    (seq-take
     (sort (nreverse out)
           (lambda (a b)
             (let ((ra (yomitan--dict-rank (nth 3 a)))
                   (rb (yomitan--dict-rank (nth 3 b))))
               (if (= ra rb)
                   (> (or (nth 4 a) 0) (or (nth 4 b) 0))
                 (< ra rb)))))
     yomitan-max-entries)))

(defun yomitan--region-text ()
  "The active region as a trimmed string, or nil."
  (when (use-region-p)
    (let ((t_ (string-trim (buffer-substring-no-properties
                            (region-beginning) (region-end)))))
      (unless (string-empty-p t_) t_))))

(defun yomitan--cjk-p (ch)
  (or (<= #x3400 ch #x9fff) (<= #xf900 ch #xfaff) (<= #x20000 ch #x3ffff)))

(defun yomitan--brief (rows)
  "First sense line from the best of ROWS, for a one-line summary."
  (when rows
    (let* ((plain (or (nth 2 (car rows)) ""))
           (sense (seq-find (lambda (l) (string-match-p "\\`[ \t]*[0-9]+\\." l))
                            (split-string plain "\n" t)))
           (head (car (split-string plain "\n" t))))
      (string-trim (replace-regexp-in-string "\\`[ \t]*[0-9]+\\.[ \t]*" ""
                                             (or sense head ""))))))

(defun yomitan--lang-group (lang)
  "Languages that gloss each other\'s characters acceptably.
Cantonese and Mandarin do; Japanese does not."
  (if (member lang '("yue" "zh")) '("yue" "zh") (list lang)))

(defun yomitan--components (term &optional lang)
  "For a multi-character TERM, (CHAR READING BRIEF) per CJK character.

LANG is the source language of the entry being shown, so that the characters
of a Japanese word are glossed in Japanese and those of a Cantonese word in
Cantonese -- without it 位相幾何学 lists its characters as wai2 soeng1 gei2 ho4
hok6.  Within a language group the usual `yomitan-dictionary-priority' applies,
so 黐 in 黐線 is glossed from CC-Canto even though 黐線 itself matched CC-CEDICT."
  (when (> (length term) 1)
    (let ((group (and lang (yomitan--lang-group lang)))
          out)
      (dolist (ch (string-to-list term))
        (when (yomitan--cjk-p ch)
          (let* ((rows (yomitan--rows (string ch)))
                 (best (or (and group
                                (seq-find (lambda (r) (member (nth 5 r) group)) rows))
                           (car rows))))
            (when best
              (push (list (string ch) (or (nth 1 best) "")
                          (yomitan--brief (list best)))
                    out)))))
      (nreverse out))))

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
    (let ((comps (yomitan--components term (nth 5 (car rows)))))
      (when comps
        (insert (propertize "characters\n" 'face 'shadow))
        (dolist (c comps)
          (insert "  "
                  (propertize (nth 0 c) 'face 'bold
                              'yomitan-term (nth 0 c)
                              'mouse-face 'highlight
                              'help-echo "RET or mouse-1: look up this character")
                  (propertize (format "  %-12s" (nth 1 c)) 'face 'font-lock-type-face)
                  (or (nth 2 c) "") "\n"))))
    (goto-char (point-min))))

(defun yomitan--show (term rows)
  (with-current-buffer (get-buffer-create "*yomitan*")
    (yomitan-mode)
    (yomitan--insert term rows)
    (setq yomitan--term term
          yomitan--lang (nth 5 (car rows)))
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
(defun yomitan-at-point (&optional single)
  "Look up at point.

With an active region, look up exactly that text -- which is how you get at
one character inside a word.  With a prefix argument, look up only the single
character after point, ignoring any longer word that would otherwise win.
Otherwise take the longest match starting at point."
  (interactive "P")
  (let* ((region (yomitan--region-text))
         (term (or region
                   (and single (char-after)
                        (string (char-after))))))
    (if term
        (let ((rows (yomitan--rows term)))
          (if rows (yomitan--show term rows)
            (message "yomitan: no entry for %s" term)))
      (let ((hit (yomitan--scan-at-point)))
        (if hit (yomitan--show (car hit) (cdr hit))
          (message "yomitan: nothing at point"))))))

(defun yomitan-follow ()
  "Look up the character named by the link at point in the *yomitan* buffer."
  (interactive)
  (let ((term (get-text-property (point) 'yomitan-term)))
    (if (not term)
        (message "yomitan: no character here")
      (let ((rows (yomitan--rows term)))
        (if rows (yomitan--show term rows)
          (message "yomitan: no entry for %s" term))))))

;;;###autoload
(defun yomitan-dicts ()
  "Report the loaded version and the imported dictionaries."
  (interactive)
  (message "yomitan %s  (priority: %s)\n%s"
           yomitan-version
           (string-join yomitan-dictionary-priority " > ")
           (mapconcat
            (lambda (r) (format "%s  (%s entries, %s)" (nth 0 r) (nth 1 r) (nth 2 r)))
            (sqlite-select (yomitan--db)
                           (concat "SELECT d.title, COUNT(t.rowid), d.imported"
                                   " FROM dict d LEFT JOIN term t ON t.dict_id = d.id"
                                   " GROUP BY d.id"))
            "\n")))

(defun yomitan--wiktionary-anchor (lang)
  "Wiktionary section for LANG, so the page opens where it is relevant."
  (cond ((member lang '("yue" "zh")) "#Chinese")
        ((equal lang "ja") "#Japanese")
        (t "")))

;;;###autoload
(defun yomitan-wiktionary (&optional external)
  "Open the current headword on Wiktionary, at the relevant language section.

This dictionary is for light lookup; Wiktionary is the level below it --
etymology, every sense, the full pronunciation table.  With a prefix argument
use an external browser instead of eww."
  (interactive "P")
  (let ((term (or yomitan--term
                  (yomitan--region-text)
                  (and (char-after) (yomitan--cjk-p (char-after))
                       (string (char-after)))
                  (read-string "Wiktionary: "))))
    (if (or (null term) (string-empty-p term))
        (message "yomitan: nothing to look up")
      (let ((url (concat (format yomitan-wiktionary-url (url-hexify-string term))
                         (yomitan--wiktionary-anchor yomitan--lang))))
        (message "%s" url)
        (funcall (if external #'browse-url yomitan-wiktionary-browser) url)))))

(defvar yomitan-mode-map (make-sparse-keymap)
  "Keymap for `yomitan-mode'.")

;; Bindings are applied on every load, NOT inside the defvar.  `defvar-keymap'
;; expands to `defvar', which does not reassign an already-bound variable, so a
;; keymap defined that way is frozen at whatever the first load of this file put
;; in it -- reloading never adds a key, and a new binding presents as
;; "w is undefined" with no way to fix it short of restarting Emacs.
(dolist (b '(("q"         quit-window)
             ("n"         next-line)
             ("p"         previous-line)
             ("RET"       yomitan-follow)
             ("<mouse-1>" yomitan-follow)
             ("w"         yomitan-wiktionary)))
  (keymap-set yomitan-mode-map (car b) (cadr b)))

(define-derived-mode yomitan-mode special-mode "Yomitan"
  "Major mode for Yomitan dictionary results.")

(provide 'yomitan)
;;; yomitan.el ends here
