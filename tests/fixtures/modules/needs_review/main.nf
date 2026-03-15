process MYTOOL_RUN {
    tag "$meta.id"
    label 'process_medium'

    conda "${moduleDir}/environment.yml"
    container "${ workflow.containerEngine == 'singularity' && !task.ext.singularity_pull_docker_container ?
        'https://depot.galaxyproject.org/singularity/mytool:1.0--h00cdaf9_0' :
        'quay.io/biocontainers/mytool:1.0--h00cdaf9_0' }"

    input:
    tuple val(meta), path(reads)

    output:
    // STYLE ISSUE: generic channel name "output"
    tuple val(meta), path("*.txt"),  emit: output
    path "versions.yml",             emit: versions, topic: 'versions'

    when:
    task.ext.when == null || task.ext.when

    script:
    def args = task.ext.args ?: ''
    // STYLE ISSUE: no task.ext.prefix despite named output files
    """
    mytool run \\
        $args \\
        --input $reads \\
        --output result.txt

    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        mytool: \$(mytool --version)
    END_VERSIONS
    """

    stub:
    """
    touch result.txt
    touch versions.yml
    """
}
