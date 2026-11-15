# Working carefully

Read the task and the files it names before you change anything. Restate the goal in one sentence so you know what
done looks like. Prefer small, reviewable steps over one large change, and keep each step focused on one concern.
Follow the conventions already used in the repository: its layout, naming, test style and tooling. Do not add
dependencies unless the task needs them. Keep the code simple and readable; clear names beat comments.

When you write tests, make them independent of each other, give them descriptive names, and make failures easy to
read. Cover the normal path and the main error paths. Run the tests after each change and fix what breaks before you
move on. Do not change behaviour that the task did not ask you to change.

If something is unclear, state your assumption and continue; ask only when a wrong guess would be expensive. When you
are done, summarise what you changed, what you checked and how, and anything you could not check. Be honest about
limits and risks. Do not claim more than the evidence shows.
