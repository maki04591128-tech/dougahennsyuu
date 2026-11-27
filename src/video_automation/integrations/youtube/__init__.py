"""YouTube への投稿（YouTube Data API v3）。

- oauth: 本人の Google アカウントで 1 回だけ許可を取り、更新用の鍵を DPAPI で暗号化して保存する
- uploader: 動画を再開できる方式（resumable）で上げ、サムネイルを付ける。既定は「非公開」
"""
