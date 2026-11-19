const WorkflowNodeRenderers = (() => {
  const renderers = new Map();

  function register(step, renderer) {
    if (renderers.has(step)) throw new Error('Duplicate workflow node renderer: ' + step);
    renderers.set(step, renderer);
  }

  function render(step, target, context) {
    const renderer = renderers.get(step);
    if (!renderer?.accepts(context)) return false;
    renderer.render(target, context);
    return true;
  }

  return {register, render};
})();
