#include <gtest/gtest.h>

#include <agent_rpc/research/workspace_planner.h>

#include <algorithm>

using namespace agent_rpc::research;

TEST(WorkspacePlannerTest, RendersPreviewWithoutCreatingDirectories) {
    WorkspacePlanRequest request;
    request.request_id = "workspace-1";
    request.workspace_root = "/lab/workspaces";
    request.job_directory_name = "job-20260623-0001";
    request.run_directory_name = "run";
    request.log_file_name = "run.log";
    request.artifact_subdirectories = {"logs", "artifacts", "snapshots"};

    const auto plan = make_workspace_plan(request);
    const auto rendered = render_workspace_plan_preview(plan);

    EXPECT_EQ(plan.planned_workspace_path, "/lab/workspaces/job-20260623-0001");
    EXPECT_EQ(plan.planned_run_directory_path,
        "/lab/workspaces/job-20260623-0001/run");
    EXPECT_EQ(plan.planned_log_path,
        "/lab/workspaces/job-20260623-0001/logs/run.log");
    ASSERT_EQ(plan.planned_artifact_paths.size(), 3);
    EXPECT_EQ(plan.planned_artifact_paths[1],
        "/lab/workspaces/job-20260623-0001/artifacts");
    EXPECT_FALSE(plan.directories_created);
    EXPECT_FALSE(plan.files_moved);
    EXPECT_FALSE(plan.server_connected);
    EXPECT_NE(rendered.find("Workspace Plan Preview"), std::string::npos);
    EXPECT_NE(rendered.find("directories_created: false"), std::string::npos);
    EXPECT_NE(rendered.find("files_moved: false"), std::string::npos);
    EXPECT_NE(rendered.find("server_connected: false"), std::string::npos);
}

TEST(WorkspacePlannerTest, RejectsPathTraversalJobDirectory) {
    WorkspacePlanRequest request;
    request.request_id = "workspace-2";
    request.workspace_root = "/lab/workspaces";
    request.job_directory_name = "../other";
    request.run_directory_name = "run";
    request.log_file_name = "run.log";

    const auto errors = validate_workspace_plan_request(request);

    EXPECT_NE(std::find(errors.begin(),
                  errors.end(),
                  "job directory name must not contain path traversal"),
        errors.end());
}

TEST(WorkspacePlannerTest, RejectsWorkspaceRootAsJobDirectory) {
    WorkspacePlanRequest request;
    request.request_id = "workspace-3";
    request.workspace_root = "/lab/workspaces";
    request.job_directory_name = "";
    request.run_directory_name = "run";
    request.log_file_name = "run.log";

    const auto errors = validate_workspace_plan_request(request);

    EXPECT_NE(std::find(errors.begin(),
                  errors.end(),
                  "job directory name is required"),
        errors.end());
}

TEST(WorkspacePlannerTest, RejectsAbsoluteEscapeJobDirectory) {
    WorkspacePlanRequest request;
    request.request_id = "workspace-4";
    request.workspace_root = "/lab/workspaces";
    request.job_directory_name = "/etc";
    request.run_directory_name = "run";
    request.log_file_name = "run.log";

    const auto errors = validate_workspace_plan_request(request);

    EXPECT_NE(std::find(errors.begin(),
                  errors.end(),
                  "job directory name must be relative"),
        errors.end());
}

TEST(WorkspacePlannerTest, RejectsEmptyAndProtectedWorkspaceRoots) {
    WorkspacePlanRequest empty_root;
    empty_root.request_id = "workspace-empty";
    empty_root.workspace_root = "";
    empty_root.job_directory_name = "job-1";
    empty_root.run_directory_name = "run";
    empty_root.log_file_name = "run.log";

    const auto empty_errors = validate_workspace_plan_request(empty_root);
    EXPECT_NE(std::find(empty_errors.begin(),
                  empty_errors.end(),
                  "workspace_root is required"),
        empty_errors.end());

    WorkspacePlanRequest protected_root;
    protected_root.request_id = "workspace-protected";
    protected_root.workspace_root = "/";
    protected_root.job_directory_name = "job-1";
    protected_root.run_directory_name = "run";
    protected_root.log_file_name = "run.log";

    const auto protected_errors =
        validate_workspace_plan_request(protected_root);
    EXPECT_NE(std::find(protected_errors.begin(),
                  protected_errors.end(),
                  "workspace_root must not be a protected location"),
        protected_errors.end());
}

TEST(WorkspacePlannerTest, RejectsUnsafeArtifactAndLogSubpaths) {
    WorkspacePlanRequest request;
    request.request_id = "workspace-5";
    request.workspace_root = "/lab/workspaces";
    request.job_directory_name = "job-1";
    request.run_directory_name = "run";
    request.log_file_name = "../run.log";
    request.artifact_subdirectories = {"artifacts", "/tmp/escape"};

    const auto errors = validate_workspace_plan_request(request);

    EXPECT_NE(std::find(errors.begin(),
                  errors.end(),
                  "log file name must not contain path traversal"),
        errors.end());
    EXPECT_NE(std::find(errors.begin(),
                  errors.end(),
                  "artifact subdirectory must be relative"),
        errors.end());
}

TEST(WorkspacePlannerTest, RejectsProtectedWorkspaceAndArtifactLabels) {
    WorkspacePlanRequest request;
    request.request_id = "workspace-6";
    request.workspace_root = "/lab/secrets";
    request.job_directory_name = "job-1";
    request.run_directory_name = "env";
    request.log_file_name = "run.log";
    request.artifact_subdirectories = {"artifacts", "shared_data"};

    const auto errors = validate_workspace_plan_request(request);

    EXPECT_NE(std::find(errors.begin(),
                  errors.end(),
                  "workspace_root must not be a protected location"),
        errors.end());
    EXPECT_NE(std::find(errors.begin(),
                  errors.end(),
                  "run directory name must not use protected labels"),
        errors.end());
    EXPECT_NE(std::find(errors.begin(),
                  errors.end(),
                  "artifact subdirectory must not use protected labels"),
        errors.end());
}
